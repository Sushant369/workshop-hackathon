"""Builds the fused claims knowledge graph in Neo4j from:
  - data/claims.db          (structured: policyholders, policies, claims)
  - data/documents/*.txt    (unstructured: adjuster narratives)

Entities (third parties, witnesses, contractors) are extracted from each
narrative via the LLM and linked into the graph, so the structured and
unstructured sources become one connected graph rather than two silos.

Run standalone: python etl/build_knowledge_graph.py
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.config import get_driver, get_llm_client, MODEL_NAME  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "claims.db"
DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "documents"

ENTITY_EXTRACTION_PROMPT = """Extract named entities from this insurance claim adjuster narrative.
Return ONLY a JSON array of objects with "name" and "type" fields. Valid types:
THIRD_PARTY (contractor/vendor/company engaged), PERSON (witness, claimant, driver, physician),
INJURY (injury or medical condition mentioned), ASSET (equipment, vehicle, structural element damaged).
Skip the insurer and the policyholder itself. If nothing qualifies, return [].

Narrative:
{text}
"""


def extract_entities(client, text: str) -> list[dict]:
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "You extract structured entities from text and reply with strict JSON only."},
                {"role": "user", "content": ENTITY_EXTRACTION_PROMPT.format(text=text)},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            content = content.split("\n", 1)[-1] if content.lower().startswith("json") else content
        entities = json.loads(content)
        return [e for e in entities if isinstance(e, dict) and e.get("name") and e.get("type")]
    except Exception as exc:  # noqa: BLE001
        print(f"  entity extraction failed: {exc}")
        return []


def load_sql_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    policyholders = [dict(r) for r in cur.execute("SELECT * FROM policyholders")]
    policies = [dict(r) for r in cur.execute("SELECT * FROM policies")]
    claims = [dict(r) for r in cur.execute("SELECT * FROM claims")]
    conn.close()
    return policyholders, policies, claims


def build_graph():
    policyholders, policies, claims = load_sql_data()
    driver = get_driver()
    client = get_llm_client()

    with driver.session() as session:
        print("Clearing existing claims-graph data...")
        session.run(
            "MATCH (n) WHERE n:PolicyHolder OR n:Policy OR n:Claim OR n:Incident "
            "OR n:Document OR n:Entity DETACH DELETE n"
        )

        print(f"Loading {len(policyholders)} policyholders...")
        session.run(
            """
            UNWIND $rows AS row
            MERGE (ph:PolicyHolder {id: row.id})
            SET ph.name = row.name, ph.industry = row.industry, ph.since = row.since
            """,
            rows=policyholders,
        )

        print(f"Loading {len(policies)} policies...")
        session.run(
            """
            UNWIND $rows AS row
            MATCH (ph:PolicyHolder {id: row.policyholder_id})
            MERGE (p:Policy {policy_number: row.policy_number})
            SET p.line_of_business = row.line_of_business,
                p.effective_date = row.effective_date,
                p.expiry_date = row.expiry_date,
                p.limit_amount = row.limit_amount,
                p.deductible = row.deductible
            MERGE (ph)-[:HOLDS]->(p)
            """,
            rows=policies,
        )

        print(f"Loading {len(claims)} claims + incidents...")
        session.run(
            """
            UNWIND $rows AS row
            MATCH (p:Policy {policy_number: row.policy_number})
            MERGE (c:Claim {claim_id: row.claim_id})
            SET c.date_of_loss = row.date_of_loss,
                c.status = row.status,
                c.cause_of_loss = row.cause_of_loss,
                c.location = row.location,
                c.incurred_amount = row.incurred_amount,
                c.paid_amount = row.paid_amount,
                c.reserve_amount = row.reserve_amount
            MERGE (p)-[:HAS_CLAIM]->(c)
            MERGE (i:Incident {claim_id: row.claim_id})
            SET i.date = row.date_of_loss, i.location = row.location,
                i.description = row.cause_of_loss + ' at ' + row.location
            MERGE (c)-[:INVOLVES]->(i)
            """,
            rows=claims,
        )

        print("Loading documents + extracting entities via LLM (this takes a bit)...")
        doc_count = 0
        entity_links = 0
        for claim in claims:
            doc_path = DOCS_DIR / f"{claim['claim_id']}.txt"
            if not doc_path.exists():
                continue
            text = doc_path.read_text(encoding="utf-8")

            session.run(
                """
                MATCH (c:Claim {claim_id: $claim_id})
                MERGE (d:Document {doc_id: $doc_id})
                SET d.type = 'adjuster_narrative', d.text = $text, d.source = $source
                MERGE (c)-[:HAS_DOCUMENT]->(d)
                """,
                claim_id=claim["claim_id"],
                doc_id=f"{claim['claim_id']}-doc",
                text=text,
                source=str(doc_path.name),
            )
            doc_count += 1

            entities = extract_entities(client, text)
            if entities:
                session.run(
                    """
                    UNWIND $entities AS e
                    MATCH (d:Document {doc_id: $doc_id})
                    MERGE (ent:Entity {name: e.name, type: e.type})
                    MERGE (d)-[:MENTIONS]->(ent)
                    """,
                    doc_id=f"{claim['claim_id']}-doc",
                    entities=entities,
                )
                entity_links += len(entities)

            print(f"  {claim['claim_id']}: {len(entities)} entities")

        print(f"Loaded {doc_count} documents, {entity_links} entity mentions.")

        print("Creating fulltext index on Document.text...")
        session.run(
            "CREATE FULLTEXT INDEX documentText IF NOT EXISTS FOR (d:Document) ON EACH [d.text]"
        )

        print("Computing SIMILAR_TO relationships between claims...")
        result = session.run(
            """
            MATCH (ph:PolicyHolder)-[:HOLDS]->(:Policy)-[:HAS_CLAIM]->(c1:Claim)
            MATCH (ph)-[:HOLDS]->(:Policy)-[:HAS_CLAIM]->(c2:Claim)
            WHERE c1.claim_id < c2.claim_id
              AND c1.cause_of_loss = c2.cause_of_loss
            WITH c1, c2,
                 CASE WHEN c1.location = c2.location THEN 0.3 ELSE 0.0 END AS locScore,
                 CASE WHEN abs(duration.inDays(date(c1.date_of_loss), date(c2.date_of_loss)).days) <= 30
                      THEN 0.3 ELSE 0.0 END AS dateScore
            WITH c1, c2, 0.4 + locScore + dateScore AS score
            WHERE score >= 0.4
            MERGE (c1)-[r:SIMILAR_TO]-(c2)
            SET r.score = score
            RETURN count(r) AS rels
            """
        ).single()
        print(f"Created {result['rels']} SIMILAR_TO relationships.")

    driver.close()
    print("Knowledge graph build complete.")


if __name__ == "__main__":
    build_graph()
