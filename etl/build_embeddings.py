"""Chunks and embeds two document sources into the knowledge graph for
GraphRAG-style semantic search:

  - Adjuster narrative Documents already in the graph (one chunk each, they're
    already short)
  - Loss run reports in data/loss_run_reports/*.txt (chunked: one summary
    chunk + one chunk per claim line)

Each Chunk carries embedding (text-embedding-3-large, via the real OpenAI key
in .env, not the GraphAcademy proxy) plus metadata: policy_number, claim_id,
loss_run_report_id (whichever apply). A Neo4j vector index is created over
Chunk.embedding so the MCP server can do native vector similarity search.

Run after build_knowledge_graph.py:
    python etl/synthetic_loss_run_reports.py
    python etl/build_embeddings.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.config import (  # noqa: E402
    get_driver, get_embeddings_client, EMBEDDINGS_MODEL, EMBEDDING_DIMENSIONS,
)

REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "loss_run_reports"

CLAIM_LINE_RE = re.compile(r"^Claim (CLM-\d+) on policy (POL-\d+):")


def embed_texts(client, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    response = client.embeddings.create(model=EMBEDDINGS_MODEL, input=texts)
    return [item.embedding for item in response.data]


def chunk_loss_run_report(report_id: str, text: str) -> list[dict]:
    """Splits a loss run report into a summary chunk (header + SUMMARY
    section) and one chunk per claim detail line."""
    lines = text.split("\n")
    try:
        detail_idx = lines.index("CLAIM DETAIL")
    except ValueError:
        return [{"text": text, "claim_id": None}]

    summary_text = "\n".join(lines[:detail_idx]).strip()
    chunks = [{"text": summary_text, "claim_id": None}]

    for line in lines[detail_idx + 1:]:
        line = line.strip()
        if not line:
            continue
        match = CLAIM_LINE_RE.match(line)
        claim_id = match.group(1) if match else None
        chunks.append({"text": line, "claim_id": claim_id})

    return chunks


def build_embeddings():
    driver = get_driver()
    client = get_embeddings_client()

    with driver.session() as session:
        print("Creating vector index on Chunk.embedding...")
        session.run(
            f"""
            CREATE VECTOR INDEX chunkEmbeddings IF NOT EXISTS
            FOR (c:Chunk) ON (c.embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {EMBEDDING_DIMENSIONS},
                `vector.similarity_function`: 'cosine'
            }}}}
            """
        )

        print("Clearing existing Chunk nodes and LossRunReport nodes...")
        session.run("MATCH (n) WHERE n:Chunk OR n:LossRunReport DETACH DELETE n")

        # --- Adjuster narrative documents: one chunk each ---
        print("Embedding adjuster narrative documents...")
        docs = list(
            session.run(
                """
                MATCH (c:Claim)-[:HAS_DOCUMENT]->(d:Document)
                MATCH (pol:Policy)-[:HAS_CLAIM]->(c)
                RETURN d.doc_id AS doc_id, d.text AS text, c.claim_id AS claim_id,
                       pol.policy_number AS policy_number
                """
            )
        )
        texts = [d["text"] for d in docs]
        embeddings = embed_texts(client, texts)
        rows = [
            {
                "chunk_id": f"{d['doc_id']}-chunk-0",
                "text": d["text"],
                "embedding": emb,
                "source_type": "adjuster_narrative",
                "policy_number": d["policy_number"],
                "claim_id": d["claim_id"],
                "loss_run_report_id": None,
                "doc_id": d["doc_id"],
            }
            for d, emb in zip(docs, embeddings)
        ]
        session.run(
            """
            UNWIND $rows AS row
            MATCH (d:Document {doc_id: row.doc_id})
            MATCH (c:Claim {claim_id: row.claim_id})
            CREATE (chunk:Chunk {
                chunk_id: row.chunk_id, text: row.text, embedding: row.embedding,
                source_type: row.source_type, policy_number: row.policy_number,
                claim_id: row.claim_id, loss_run_report_id: row.loss_run_report_id
            })
            MERGE (d)-[:HAS_CHUNK]->(chunk)
            MERGE (chunk)-[:ABOUT_CLAIM]->(c)
            """,
            rows=rows,
        )
        print(f"  embedded {len(rows)} adjuster narrative chunks")

        # --- Loss run reports: summary chunk + per-claim chunks ---
        print("Embedding loss run reports...")
        total_lrr_chunks = 0
        for report_path in sorted(REPORTS_DIR.glob("*.txt")):
            report_id = report_path.stem
            text = report_path.read_text(encoding="utf-8")

            header_line = text.split("\n")[2]  # "Policyholder: <name> (<id>)"
            ph_id_match = re.search(r"\((PH-\d+)\)", header_line)
            policyholder_id = ph_id_match.group(1) if ph_id_match else None

            policy_line = next(l for l in text.split("\n") if l.startswith("Policies:"))
            policy_numbers = re.findall(r"(POL-\d+)", policy_line)

            session.run(
                """
                MATCH (ph:PolicyHolder {id: $policyholder_id})
                MERGE (lrr:LossRunReport {report_id: $report_id})
                SET lrr.text = $text, lrr.policyholder_id = $policyholder_id
                MERGE (lrr)-[:FOR_POLICYHOLDER]->(ph)
                WITH lrr
                UNWIND $policy_numbers AS pn
                MATCH (pol:Policy {policy_number: pn})
                MERGE (lrr)-[:FOR_POLICY]->(pol)
                """,
                report_id=report_id, text=text, policyholder_id=policyholder_id,
                policy_numbers=policy_numbers,
            )

            chunks = chunk_loss_run_report(report_id, text)
            chunk_texts = [c["text"] for c in chunks]
            chunk_embeddings = embed_texts(client, chunk_texts)

            rows = [
                {
                    "chunk_id": f"{report_id}-chunk-{i}",
                    "text": c["text"],
                    "embedding": emb,
                    "source_type": "loss_run_report",
                    "policy_number": policy_numbers[0] if policy_numbers else None,
                    "claim_id": c["claim_id"],
                    "loss_run_report_id": report_id,
                }
                for i, (c, emb) in enumerate(zip(chunks, chunk_embeddings))
            ]
            session.run(
                """
                UNWIND $rows AS row
                MATCH (lrr:LossRunReport {report_id: row.loss_run_report_id})
                CREATE (chunk:Chunk {
                    chunk_id: row.chunk_id, text: row.text, embedding: row.embedding,
                    source_type: row.source_type, policy_number: row.policy_number,
                    claim_id: row.claim_id, loss_run_report_id: row.loss_run_report_id
                })
                MERGE (lrr)-[:HAS_CHUNK]->(chunk)
                """,
                rows=rows,
            )
            claim_rows = [r for r in rows if r["claim_id"]]
            if claim_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (chunk:Chunk {chunk_id: row.chunk_id})
                    MATCH (c:Claim {claim_id: row.claim_id})
                    MATCH (lrr:LossRunReport {report_id: row.loss_run_report_id})
                    MERGE (chunk)-[:ABOUT_CLAIM]->(c)
                    MERGE (lrr)-[:COVERS_CLAIM]->(c)
                    """,
                    rows=claim_rows,
                )
            total_lrr_chunks += len(rows)
            print(f"  {report_id}: {len(rows)} chunks")

        print(f"  embedded {total_lrr_chunks} loss run report chunks")

    driver.close()
    print("Embeddings build complete.")


if __name__ == "__main__":
    build_embeddings()
