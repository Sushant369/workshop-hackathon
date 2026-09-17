"""Cypher query functions backing the MCP tools. Each function takes a Neo4j
driver and returns plain dicts/lists (JSON-serializable) for the MCP layer."""

from typing import Optional


def resolve_entity(driver, query_text: str, entity_type: Optional[str] = None, limit: int = 5) -> list[dict]:
    """Fuzzy-resolve a free-text mention (e.g. "Meridian", "the manufacturing
    account", a misspelled name) to canonical PolicyHolder/Policy/Claim nodes.
    Meant to run before other tools so downstream queries use exact ids/names
    instead of guessing at string matches themselves.
    """
    candidates: list[dict] = []
    with driver.session() as session:
        if entity_type in (None, "PolicyHolder"):
            rows = session.run(
                """
                MATCH (ph:PolicyHolder)
                WHERE toLower(ph.name) CONTAINS toLower($q) OR toLower(ph.id) = toLower($q)
                RETURN ph.id AS id, ph.name AS name, ph.industry AS industry,
                       CASE WHEN toLower(ph.name) = toLower($q) THEN 1.0
                            WHEN toLower(ph.name) STARTS WITH toLower($q) THEN 0.8
                            ELSE 0.5 END AS confidence
                ORDER BY confidence DESC
                LIMIT $limit
                """,
                q=query_text, limit=limit,
            )
            candidates += [{"entity_type": "PolicyHolder", **dict(r)} for r in rows]

        if entity_type in (None, "Policy"):
            rows = session.run(
                """
                MATCH (ph:PolicyHolder)-[:HOLDS]->(pol:Policy)
                WHERE toLower(pol.policy_number) CONTAINS toLower($q)
                   OR toLower(pol.line_of_business) CONTAINS toLower($q)
                RETURN pol.policy_number AS id, ph.name AS policyholder,
                       pol.line_of_business AS line_of_business, 0.7 AS confidence
                LIMIT $limit
                """,
                q=query_text, limit=limit,
            )
            candidates += [{"entity_type": "Policy", **dict(r)} for r in rows]

        if entity_type in (None, "Claim"):
            rows = session.run(
                """
                MATCH (ph:PolicyHolder)-[:HOLDS]->(:Policy)-[:HAS_CLAIM]->(c:Claim)
                WHERE toLower(c.claim_id) = toLower($q) OR toLower(c.cause_of_loss) CONTAINS toLower($q)
                RETURN c.claim_id AS id, ph.name AS policyholder, c.cause_of_loss AS cause_of_loss,
                       c.date_of_loss AS date_of_loss,
                       CASE WHEN toLower(c.claim_id) = toLower($q) THEN 1.0 ELSE 0.6 END AS confidence
                ORDER BY confidence DESC
                LIMIT $limit
                """,
                q=query_text, limit=limit,
            )
            candidates += [{"entity_type": "Claim", **dict(r)} for r in rows]

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates[:limit]


def get_loss_run(driver, policyholder: str, limit: Optional[int] = None) -> dict:
    query = """
    MATCH (ph:PolicyHolder)
    WHERE toLower(ph.name) CONTAINS toLower($policyholder) OR ph.id = $policyholder
    MATCH (ph)-[:HOLDS]->(pol:Policy)-[:HAS_CLAIM]->(c:Claim)
    RETURN ph.name AS policyholder, ph.industry AS industry,
           pol.policy_number AS policy_number, pol.line_of_business AS line_of_business,
           c.claim_id AS claim_id, c.date_of_loss AS date_of_loss, c.status AS status,
           c.cause_of_loss AS cause_of_loss, c.location AS location,
           c.incurred_amount AS incurred_amount, c.paid_amount AS paid_amount,
           c.reserve_amount AS reserve_amount
    ORDER BY c.date_of_loss DESC
    """
    limited_query = query + ("LIMIT $limit" if limit else "")
    with driver.session() as session:
        rows = [dict(r) for r in session.run(limited_query, policyholder=policyholder, limit=limit)]
    if not rows:
        return {"policyholder": policyholder, "claims": [], "note": "No matching policyholder or claims found."}
    with driver.session() as session:
        total_claim_count = session.run(
            """
            MATCH (ph:PolicyHolder)
            WHERE toLower(ph.name) CONTAINS toLower($policyholder) OR ph.id = $policyholder
            MATCH (ph)-[:HOLDS]->(:Policy)-[:HAS_CLAIM]->(c:Claim)
            RETURN count(c) AS total
            """,
            policyholder=policyholder,
        ).single()["total"]
    return {
        "policyholder": rows[0]["policyholder"],
        "industry": rows[0]["industry"],
        "total_claim_count": total_claim_count,
        "returned_count": len(rows),
        "claims": [
            {k: r[k] for k in (
                "claim_id", "policy_number", "line_of_business", "date_of_loss", "status",
                "cause_of_loss", "location", "incurred_amount", "paid_amount", "reserve_amount",
            )}
            for r in rows
        ],
    }


def get_claim_details(driver, claim_id: str) -> dict:
    query = """
    MATCH (c:Claim {claim_id: $claim_id})
    OPTIONAL MATCH (pol:Policy)-[:HAS_CLAIM]->(c)
    OPTIONAL MATCH (ph:PolicyHolder)-[:HOLDS]->(pol)
    OPTIONAL MATCH (c)-[:HAS_DOCUMENT]->(d:Document)
    OPTIONAL MATCH (d)-[:MENTIONS]->(ent:Entity)
    RETURN c AS claim, pol.policy_number AS policy_number, ph.name AS policyholder,
           collect(DISTINCT d.text) AS documents,
           collect(DISTINCT {name: ent.name, type: ent.type}) AS entities
    """
    with driver.session() as session:
        record = session.run(query, claim_id=claim_id).single()
    if not record:
        return {"error": f"Claim {claim_id} not found."}
    claim = dict(record["claim"])
    entities = [e for e in record["entities"] if e.get("name")]
    return {
        "claim": claim,
        "policy_number": record["policy_number"],
        "policyholder": record["policyholder"],
        "documents": record["documents"],
        "entities": entities,
    }


def search_claims(
    driver,
    cause_of_loss: Optional[str] = None,
    min_amount: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    clauses = []
    params: dict = {}
    if cause_of_loss:
        clauses.append("toLower(c.cause_of_loss) CONTAINS toLower($cause_of_loss)")
        params["cause_of_loss"] = cause_of_loss
    if min_amount is not None:
        clauses.append("c.incurred_amount >= $min_amount")
        params["min_amount"] = min_amount
    if date_from:
        clauses.append("c.date_of_loss >= $date_from")
        params["date_from"] = date_from
    if date_to:
        clauses.append("c.date_of_loss <= $date_to")
        params["date_to"] = date_to
    if status:
        clauses.append("toLower(c.status) = toLower($status)")
        params["status"] = status

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
    MATCH (ph:PolicyHolder)-[:HOLDS]->(pol:Policy)-[:HAS_CLAIM]->(c:Claim)
    {where}
    RETURN ph.name AS policyholder, pol.policy_number AS policy_number,
           c.claim_id AS claim_id, c.date_of_loss AS date_of_loss, c.status AS status,
           c.cause_of_loss AS cause_of_loss, c.location AS location,
           c.incurred_amount AS incurred_amount
    ORDER BY c.date_of_loss DESC
    LIMIT 50
    """
    with driver.session() as session:
        return [dict(r) for r in session.run(query, **params)]


def find_similar_claims(driver, claim_id: str, limit: int = 5) -> dict:
    query = """
    MATCH (c1:Claim {claim_id: $claim_id})-[r:SIMILAR_TO]-(c2:Claim)
    OPTIONAL MATCH (pol:Policy)-[:HAS_CLAIM]->(c2)
    OPTIONAL MATCH (ph:PolicyHolder)-[:HOLDS]->(pol)
    RETURN c2.claim_id AS claim_id, ph.name AS policyholder, c2.date_of_loss AS date_of_loss,
           c2.cause_of_loss AS cause_of_loss, c2.location AS location,
           c2.incurred_amount AS incurred_amount, r.score AS similarity_score
    ORDER BY r.score DESC
    LIMIT $limit
    """
    with driver.session() as session:
        rows = [dict(r) for r in session.run(query, claim_id=claim_id, limit=limit)]
    return {"claim_id": claim_id, "similar_claims": rows}


def search_claim_documents(driver, query_text: str, limit: int = 10) -> list[dict]:
    query = """
    CALL db.index.fulltext.queryNodes('documentText', $query_text) YIELD node, score
    MATCH (c:Claim)-[:HAS_DOCUMENT]->(node)
    OPTIONAL MATCH (pol:Policy)-[:HAS_CLAIM]->(c)
    OPTIONAL MATCH (ph:PolicyHolder)-[:HOLDS]->(pol)
    RETURN c.claim_id AS claim_id, ph.name AS policyholder, score,
           node.text AS document_text
    ORDER BY score DESC
    LIMIT $limit
    """
    with driver.session() as session:
        return [dict(r) for r in session.run(query, query_text=query_text, limit=limit)]


def get_risk_summary(driver, policyholder: str) -> dict:
    query = """
    MATCH (ph:PolicyHolder)
    WHERE toLower(ph.name) CONTAINS toLower($policyholder) OR ph.id = $policyholder
    MATCH (ph)-[:HOLDS]->(pol:Policy)-[:HAS_CLAIM]->(c:Claim)
    WITH ph, c, substring(c.date_of_loss, 0, 4) AS year
    RETURN ph.name AS policyholder,
           count(c) AS claim_count,
           sum(c.incurred_amount) AS total_incurred,
           avg(c.incurred_amount) AS avg_severity,
           collect(DISTINCT c.cause_of_loss) AS causes,
           collect({year: year, cause: c.cause_of_loss, amount: c.incurred_amount}) AS claim_breakdown
    """
    with driver.session() as session:
        record = session.run(query, policyholder=policyholder).single()
    if not record:
        return {"policyholder": policyholder, "note": "No matching policyholder found."}

    breakdown = record["claim_breakdown"]
    by_year: dict = {}
    cause_counts: dict = {}
    for item in breakdown:
        by_year[item["year"]] = by_year.get(item["year"], 0) + 1
        cause_counts[item["cause"]] = cause_counts.get(item["cause"], 0) + 1
    top_causes = sorted(cause_counts.items(), key=lambda x: x[1], reverse=True)[:3]

    return {
        "policyholder": record["policyholder"],
        "claim_count": record["claim_count"],
        "total_incurred": record["total_incurred"],
        "avg_severity": round(record["avg_severity"], 2) if record["avg_severity"] else 0,
        "claims_per_year": by_year,
        "top_causes": [{"cause": c, "count": n} for c, n in top_causes],
    }


def ingest_claim_document(driver, llm_client, model_name: str, claim_id: str, text: str) -> dict:
    from etl.build_knowledge_graph import extract_entities  # local import to avoid MCP-time dependency cycle

    with driver.session() as session:
        claim_exists = session.run(
            "MATCH (c:Claim {claim_id: $claim_id}) RETURN c.claim_id AS id", claim_id=claim_id
        ).single()
        if not claim_exists:
            return {"error": f"Claim {claim_id} not found; cannot attach document."}

        doc_id = f"{claim_id}-doc-{session.run('RETURN randomUUID() AS id').single()['id'][:8]}"
        session.run(
            """
            MATCH (c:Claim {claim_id: $claim_id})
            MERGE (d:Document {doc_id: $doc_id})
            SET d.type = 'manual_ingest', d.text = $text, d.source = 'mcp_ingest'
            MERGE (c)-[:HAS_DOCUMENT]->(d)
            """,
            claim_id=claim_id,
            doc_id=doc_id,
            text=text,
        )

        entities = extract_entities(llm_client, text)
        if entities:
            session.run(
                """
                UNWIND $entities AS e
                MATCH (d:Document {doc_id: $doc_id})
                MERGE (ent:Entity {name: e.name, type: e.type})
                MERGE (d)-[:MENTIONS]->(ent)
                """,
                doc_id=doc_id,
                entities=entities,
            )

    return {"claim_id": claim_id, "doc_id": doc_id, "entities_extracted": entities}


def semantic_search(
    driver,
    embeddings_client,
    embeddings_model: str,
    query_text: str,
    policy_number: Optional[str] = None,
    claim_id: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """Vector similarity search over embedded chunks (adjuster narratives +
    loss run reports), optionally filtered to a policy_number/claim_id."""
    response = embeddings_client.embeddings.create(model=embeddings_model, input=[query_text])
    query_embedding = response.data[0].embedding

    clauses = []
    params: dict = {"limit": limit, "embedding": query_embedding, "k": limit * 4}
    if policy_number:
        clauses.append("node.policy_number = $policy_number")
        params["policy_number"] = policy_number
    if claim_id:
        clauses.append("node.claim_id = $claim_id")
        params["claim_id"] = claim_id
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    query = f"""
    CALL db.index.vector.queryNodes('chunkEmbeddings', $k, $embedding) YIELD node, score
    {where}
    RETURN node.chunk_id AS chunk_id, node.text AS text, node.source_type AS source_type,
           node.policy_number AS policy_number, node.claim_id AS claim_id,
           node.loss_run_report_id AS loss_run_report_id, score
    ORDER BY score DESC
    LIMIT $limit
    """
    with driver.session() as session:
        return [dict(r) for r in session.run(query, **params)]


def get_loss_run_report(driver, policyholder_or_report_id: str) -> dict:
    """Fetch a policyholder's loss run report: full text plus the claims it covers."""
    query = """
    MATCH (lrr:LossRunReport)
    WHERE lrr.report_id = $q OR lrr.policyholder_id = $q
    OPTIONAL MATCH (ph:PolicyHolder)<-[:FOR_POLICYHOLDER]-(lrr)
    WHERE ph IS NOT NULL OR toLower(ph.name) CONTAINS toLower($q)
    RETURN lrr.report_id AS report_id, lrr.text AS text, ph.name AS policyholder
    """
    fallback_query = """
    MATCH (ph:PolicyHolder)<-[:FOR_POLICYHOLDER]-(lrr:LossRunReport)
    WHERE toLower(ph.name) CONTAINS toLower($q)
    RETURN lrr.report_id AS report_id, lrr.text AS text, ph.name AS policyholder
    """
    with driver.session() as session:
        record = session.run(query, q=policyholder_or_report_id).single()
        if not record:
            record = session.run(fallback_query, q=policyholder_or_report_id).single()
    if not record:
        return {"note": f"No loss run report found matching '{policyholder_or_report_id}'."}
    return dict(record)
