# Neo4j Insurance MCP Server

A local MCP server exposing underwriting-analysis tools over a Neo4j knowledge graph. This file covers **how to run it**. For what it is, why it matters, the full data model, and a demo script, see [HACKATHON.md](HACKATHON.md).

## Prerequisites

- Python 3.10+
- A Neo4j Aura instance (the free tier works)
- A chat-completions LLM key — any OpenAI-compatible endpoint (real OpenAI or a proxy) — used for entity extraction
- A **real OpenAI API key with billing enabled** — used for embeddings (`text-embedding-3-large`); this must hit `api.openai.com` directly, not a proxy

## 1. Configure

```bash
cp .env.example .env
```

Fill in `.env` with your Neo4j credentials and the two LLM keys — see the comments in `.env.example` for what each one is for. **Never commit `.env`** (it's already gitignored — it holds live credentials).

## 2. Install dependencies

Make sure `pip` and `python` resolve to the same environment before installing (if you have multiple Python installs, `pip` alone can silently target the wrong one):

```bash
python -m pip install -r requirements.txt
```

## 3. Build the graph

Run these five scripts once, **in order**:

```bash
python etl/synthetic_sql_data.py         # structured data → data/claims.db
python etl/synthetic_documents.py        # adjuster narratives → data/documents/
python etl/synthetic_loss_run_reports.py # loss run reports → data/loss_run_reports/
python etl/build_knowledge_graph.py      # loads everything into Neo4j + LLM entity extraction
python etl/build_embeddings.py           # chunks + embeds + creates the vector index
```

- Step 4 makes ~45 LLM calls (one per narrative) — takes a few minutes.
- Step 5 calls the real OpenAI embeddings API — budget for a small one-time cost (98 chunks).
- Every script clears and rebuilds only its own data, so re-running any of them individually is safe.

## 4. Run the MCP server

```bash
python mcp_server/server.py --http
```

This starts the server on `http://127.0.0.1:8000/mcp` and keeps running in your terminal — leave it open while you use it. (Running with no `--http` flag instead starts it in stdio mode, for clients that spawn the process themselves — see "Alternative: stdio" below.)

Sanity-check it's alive in another terminal:

```bash
curl -s http://127.0.0.1:8000/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"check","version":"0.0.1"}}}'
```

A valid JSON-RPC response with `"serverInfo":{"name":"Neo4j_insurance",...}` means it's working.

## 5. Add it to your agent

### Claude Code

**Option A — via the `/mcp` UI (easiest):**
1. Type `/mcp` in your Claude Code chat.
2. Choose "Add MCP server" → select **HTTP (remote)** as the transport (*not* "Local command (stdio)").
3. Name: `neo4j-insurance`. URL: `http://127.0.0.1:8000/mcp`.
4. Save — it should connect immediately since the server is already running.

**Option B — via `.mcp.json`:** this repo already has a project-scoped entry committed:
```json
"neo4j-insurance": {
  "type": "http",
  "url": "http://127.0.0.1:8000/mcp"
}
```
With the server running, start (or restart) a Claude Code session in this workspace and it'll be picked up automatically. If you already have a session open, `/mcp` won't always hot-reload a newly-added entry — reload the window or start a new chat.

**Option C — via CLI:**
```bash
claude mcp add --transport http neo4j-insurance http://127.0.0.1:8000/mcp
```

### Any other MCP-compatible agent (Cursor, Claude Desktop, Windsurf, etc.)

Every MCP client's "add server" flow asks for the same three things:
- **Transport**: HTTP / remote
- **Name**: `neo4j-insurance` (or anything you like)
- **URL**: `http://127.0.0.1:8000/mcp`

Check your specific agent's MCP settings for exactly where these go — the values themselves are the same everywhere.

### Alternative: stdio (client manages the process for you)

If your MCP client spawns local servers itself instead of connecting to a URL, register it as a local command instead of running it yourself:

```json
"neo4j-insurance": {
  "command": "python",
  "args": ["/absolute/path/to/mcp_server/server.py"]
}
```

(No `--http` flag — omitting it defaults to stdio.) With this option, don't run step 4 manually; the client starts and stops the process for you automatically whenever it connects.

## 6. Try it

Once connected, just ask questions — the agent picks the right tool on its own:

- "What's the claim history on Meridian Manufacturing before I quote their renewal?"
- "Are there any claims that look suspiciously similar to each other?"
- "Has any account had a recurring water damage problem?"
- "Pull the loss run report for Cascade Construction."
- "Log a note on claim CLM-00043 — the insured confirmed the fire was caused by a faulty breaker panel."

If the project skill is installed (`.claude/skills/neo4j-insurance/SKILL.md`, included in this repo), Claude Code automatically decides which of the 9 tools to call — and in what order — without you needing to name a tool yourself.

See [HACKATHON.md](HACKATHON.md) for the full tool catalog, graph data model, and a demo Q&A script with expected answers grounded in the seeded data.

## Project structure

```
data/
  claims.db                 # synthetic SQLite: policyholders, policies, claims
  documents/*.txt           # synthetic adjuster narratives (one per claim)
  loss_run_reports/*.txt    # synthetic loss run reports (one per policyholder)
etl/
  synthetic_sql_data.py
  synthetic_documents.py
  synthetic_loss_run_reports.py
  build_knowledge_graph.py
  build_embeddings.py
mcp_server/
  config.py                 # .env loading, Neo4j driver, LLM/embeddings clients
  graph_queries.py           # Cypher functions backing each tool
  server.py                  # FastMCP tool registration + stdio/HTTP entrypoint
.claude/skills/neo4j-insurance/SKILL.md  # tool-routing guidance for the agent
requirements.txt
.mcp.json                    # registers neo4j-mcp, neo4j-graphacademy, neo4j-insurance
HACKATHON.md                 # full project write-up + demo script
```

## Troubleshooting

- **`ModuleNotFoundError: No module named 'mcp_server'`** — you're likely running a script from the wrong working directory, or `pip`/`python` point to different environments. Always run commands from the project root, and use `python -m pip install ...` to match the interpreter you'll run scripts with.
- **GraphAcademy proxy returns a 403 "session has gone idle"** — only relevant if you're using the GraphAcademy proxy as your chat-completions endpoint; reactivate by opening any GraphAcademy lesson or calling any `neo4j-graphacademy` MCP tool, then retry.
- **Port 8000 already in use** — either stop whatever's using it, or change the port in `mcp_server/server.py`'s `mcp.run(transport="http", host="127.0.0.1", port=8000)` call and update the URL you register accordingly.
