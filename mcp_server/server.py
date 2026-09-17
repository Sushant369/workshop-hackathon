"""Neo4j Insurance MCP server: exposes underwriting-analysis tools over the
claims knowledge graph (Neo4j), fusing structured claims/policy data with
entities extracted from unstructured adjuster narratives.

Run the knowledge graph build first:

    python etl/build_knowledge_graph.py

Then run this server via stdio (registered in .mcp.json as `neo4j-insurance`).
"""

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP

from mcp_server import graph_queries
from mcp_server.config import get_driver, get_llm_client, get_embeddings_client, MODEL_NAME, EMBEDDINGS_MODEL

mcp = FastMCP("Neo4j_insurance")

_driver = get_driver()
_llm_client = get_llm_client()
_embeddings_client = get_embeddings_client()


@mcp.tool()
def resolve_entity(query_text: str, entity_type: Optional[str] = None, limit: int = 5) -> list[dict]:
    """Resolve a free-text mention to canonical entities in the knowledge graph
    before querying further. Use this first when the user's wording is
    ambiguous, partial, or possibly misspelled (e.g. "Meridian", "the
    manufacturing account", a claim number fragment) so downstream tool calls
    use exact ids/names instead of guessing.

    Args:
        query_text: Free-text mention to resolve, e.g. "Meridian" or "CLM-00012".
        entity_type: Restrict to one of "PolicyHolder", "Policy", "Claim". Omit to search all.
        limit: Max number of candidate matches to return.
    """
    return graph_queries.resolve_entity(_driver, query_text, entity_type=entity_type, limit=limit)


@mcp.tool()
def get_loss_run(policyholder: str, limit: Optional[int] = None) -> dict:
    """Get the claim/loss history for a policyholder (loss run), most recent first.

    Args:
        policyholder: Policyholder name (partial match ok) or policyholder id (e.g. PH-001).
            If ambiguous, call resolve_entity first to get the exact name/id.
        limit: Max number of most-recent claims to return. Omit for full history.
    """
    return graph_queries.get_loss_run(_driver, policyholder, limit=limit)


@mcp.tool()
def get_claim_details(claim_id: str) -> dict:
    """Get a merged view of a single claim: structured fields, linked adjuster
    narrative documents, and entities extracted from those documents.

    Args:
        claim_id: Claim identifier, e.g. CLM-00001.
    """
    return graph_queries.get_claim_details(_driver, claim_id)


@mcp.tool()
def search_claims(
    cause_of_loss: Optional[str] = None,
    min_amount: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """Search claims across all policyholders with optional filters.

    Args:
        cause_of_loss: Partial match on cause of loss, e.g. "Water Damage".
        min_amount: Minimum incurred amount.
        date_from: ISO date lower bound (YYYY-MM-DD) on date of loss.
        date_to: ISO date upper bound (YYYY-MM-DD) on date of loss.
        status: Claim status, e.g. "Open", "Closed", "Reopened".
    """
    return graph_queries.search_claims(
        _driver,
        cause_of_loss=cause_of_loss,
        min_amount=min_amount,
        date_from=date_from,
        date_to=date_to,
        status=status,
    )


@mcp.tool()
def find_similar_claims(claim_id: str, limit: int = 5) -> dict:
    """Find claims graph-similar to a given claim (same cause of loss, same
    policyholder, overlapping location/timing) — useful for spotting patterns,
    recurring exposures, or potential duplicate/fraudulent submissions.

    Args:
        claim_id: Claim identifier to find similar claims for.
        limit: Max number of similar claims to return.
    """
    return graph_queries.find_similar_claims(_driver, claim_id, limit=limit)


@mcp.tool()
def search_claim_documents(query_text: str, limit: int = 10) -> list[dict]:
    """Full-text search over unstructured adjuster narrative documents linked
    to claims. Returns matching claim ids and document excerpts.

    Args:
        query_text: Free-text search query, e.g. "water damage recurring".
        limit: Max number of results.
    """
    return graph_queries.search_claim_documents(_driver, query_text, limit=limit)


@mcp.tool()
def get_risk_summary(policyholder: str) -> dict:
    """Get an aggregated risk summary for a policyholder: claim count, total
    incurred, average severity, claims per year, and top causes of loss.

    Args:
        policyholder: Policyholder name (partial match ok) or policyholder id.
    """
    return graph_queries.get_risk_summary(_driver, policyholder)


@mcp.tool()
def semantic_search(
    query_text: str,
    policy_number: Optional[str] = None,
    claim_id: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """GraphRAG semantic search: vector similarity search over embedded claim
    documents (adjuster narratives) and loss run report chunks. Use this for
    conceptual/fuzzy questions ("has this account had recurring water issues?")
    where exact keyword matching (search_claim_documents) would miss relevant
    text. Optionally scope to a policy_number or claim_id.

    Args:
        query_text: Natural-language question or description to search for.
        policy_number: Optional filter to only this policy's chunks.
        claim_id: Optional filter to only this claim's chunks.
        limit: Max number of chunks to return.
    """
    return graph_queries.semantic_search(
        _driver, _embeddings_client, EMBEDDINGS_MODEL, query_text,
        policy_number=policy_number, claim_id=claim_id, limit=limit,
    )


@mcp.tool()
def get_loss_run_report(policyholder_or_report_id: str) -> dict:
    """Fetch a policyholder's full loss run report (the formal underwriting
    summary document), including the claims it covers.

    Args:
        policyholder_or_report_id: Policyholder name (partial match ok) or
            exact loss run report id, e.g. "LRR-PH-001-2026".
    """
    return graph_queries.get_loss_run_report(_driver, policyholder_or_report_id)


@mcp.tool()
def ingest_claim_document(claim_id: str, text: str) -> dict:
    """Attach a new unstructured document (adjuster note, loss narrative, etc.)
    to an existing claim, extract entities from it via LLM, and merge them
    into the knowledge graph.

    Args:
        claim_id: Existing claim identifier to attach the document to.
        text: Free-text document content.
    """
    return graph_queries.ingest_claim_document(_driver, _llm_client, MODEL_NAME, claim_id, text)


if __name__ == "__main__":
    import sys

    if "--http" in sys.argv:
        # Run yourself in a terminal: python mcp_server/server.py --http
        # Then register in .mcp.json as {"type": "http", "url": "http://127.0.0.1:8000/mcp"}
        mcp.run(transport="http", host="127.0.0.1", port=8000)
    else:
        # Default: stdio. The MCP client (Claude Code) spawns this process
        # itself per the "command"/"args" in .mcp.json — you don't run this manually.
        mcp.run(transport="stdio")
