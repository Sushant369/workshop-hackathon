---
name: neo4j-insurance
description: Use when answering underwriting questions about policyholders, policies, claims, loss history, or risk that this project's Neo4j claims knowledge graph can answer. Decides which neo4j-insurance MCP tool(s) to call and in what order — covers loss runs, claim search, similarity/duplicate detection, fulltext vs. semantic document search, risk summaries, loss run reports, and live document ingestion.
---

# Neo4j Insurance MCP: tool routing

The `neo4j-insurance` MCP server exposes 9 tools over a knowledge graph that
fuses structured claims/policy data (SQL source) with unstructured adjuster
narratives and loss run reports (embedded for vector search). Each tool's own
description covers its parameters — this skill covers **which one to reach
for and in what order**, since several tools look similar but answer
different question shapes.

## Step 0 — resolve ambiguous entities first

If the user's wording for a policyholder, policy, or claim is a partial
name, nickname, abbreviation, or possibly misspelled (e.g. "Meridian", "the
manufacturing account", "PH-0001" vs "PH-001"), call `resolve_entity` first
and use the exact name/id it returns in every subsequent call. Don't guess
at string matches yourself — the graph tools do exact/CONTAINS matching, and
a wrong guess silently returns empty results instead of an error.

Skip this step when the user already gave an exact id (e.g. `CLM-00043`,
`POL-0001`) or an unambiguous full policyholder name.

## Step 1 — pick the tool by question shape

| Question shape | Tool | Notes |
|---|---|---|
| "Show me claim history / loss run for X" | `get_loss_run` | Pass `limit` for "last N claims"; always check `total_claim_count` vs `returned_count` in the result before saying "these are all the claims" |
| "Details on claim CLM-xxxxx" | `get_claim_details` | Merged structured + narrative + entities view of one claim |
| "Find claims where cause=X / amount>Y / date range / status=Z" | `search_claims` | Structured filtering across all policyholders |
| "Claims similar to this one" / "duplicate submissions" / "recurring pattern" | `find_similar_claims` | Graph-based (shared cause + location + timing), not text search |
| "Find claims mentioning exact phrase/keyword" | `search_claim_documents` | Fulltext/keyword — use when the user's wording is likely to appear verbatim in a narrative |
| Conceptual or fuzzy question with no exact keyword ("has this account had ongoing water issues?", "any signs of fire risk here?") | `semantic_search` | Vector similarity — catches meaning even when the query words never appear in the text. Prefer this over `search_claim_documents` whenever the question is descriptive rather than a keyword lookup. Optionally scope with `policy_number` or `claim_id`. |
| "What's the risk profile / claim frequency / total incurred for X" | `get_risk_summary` | Aggregates: count, total incurred, avg severity, claims per year, top causes |
| "Get me the loss run report / LRR for X" | `get_loss_run_report` | The formal per-policyholder underwriting document, distinct from `get_loss_run`'s structured data |
| "Add this note/document to claim X" | `ingest_claim_document` | Live-attaches a document, extracts entities via LLM, merges into the graph |

## Step 2 — combine tools for underwriting-style questions

Real underwriting questions ("should we renew this account?", "what's our
exposure here?", "give me the full picture on this policyholder") are rarely
answered by one tool. Default combination for a full account review:

1. `resolve_entity` (if needed)
2. `get_risk_summary` — the headline numbers and trend
3. `find_similar_claims` on their most recent or largest claim — surfaces
   recurring exposure patterns `get_risk_summary`'s aggregates can hide
4. `get_loss_run_report` — the formal document an underwriter would actually
   attach to a renewal file
5. `semantic_search` scoped to that policyholder's `policy_number`, if the
   user asked about a specific concern (e.g. "any water damage issues?")

Don't run all of these reflexively for a simple factual lookup (e.g. "what's
claim CLM-00043's status?") — go straight to `get_claim_details`.

## Grounding

Always answer from what the tools actually return. If `get_loss_run` returns
`"note": "No matching policyholder or claims found."` or a tool returns an
empty list, say so plainly — don't fill in plausible-sounding claim details
that didn't come back from the graph.
