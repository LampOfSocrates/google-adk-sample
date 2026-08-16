# LATEST

## What this is
A holding repo for four independent Google ADK agents (Travel Planner, PDF
Insight, Text-to-Diagram, Graph Builder), sharing one model layer that swaps
between mock/gemini/openai/deepseek/bedrock backends. FastAPI server + Streamlit
client provide a Claude-style chat UI over all four agents.

## Where it runs
Local dev via `./local_run.sh` (adk web, or `server`+`ui`), and deployed via
Docker Compose onto a small (~916 MB RAM) EC2 box co-hosting other apps —
Streamlit UI on port 8501, memory-capped with swap overflow (`docker-compose.yml`,
`deploy/setup-swap.sh`).

## Features
- Travel Planner: coordinator routing to weather sub-agent + web-search agent-tool.
- PDF Insight: multi-mode PDF Q&A (extract tables, NL Q&A, SQL) over one PDF or a DuckDB corpus.
- Text-to-Diagram: prose to knowledge-graph triads to mermaid diagram.
- Graph Builder: accreting conversational knowledge graph (Cartograph entity resolver).
- Streamlit UI: Explorer-style zoomable/clickable agent tree, per-agent system-prompt editor, DuckDB conversation history, debug tab.
- Docker/Compose deploy path for co-hosting on a memory-constrained EC2 box.

## Recently tried
- 2026-07-09: agents — surface tool docstrings + agent system prompts in the tree UI; editor now edits only the system prompt.
- 2026-07-08: ui(agents) — unit tests for agent-tree helpers (11 passed).
- 2026-07-08: ui(agents) — size tree/diagram to hug content instead of a fixed 300px floor.
- 2026-07-08: ui — switch page layout centered to wide so chat fills width.
- 2026-07-08: ui(agents) — keep edit/save flow prominent under the new tree view.

## Next
- Fix offline `auto`-mode corpus routing for PDF Insight (mock backend always picks `extract_tables`; needs a routing heuristic in `shared/mock_llm.py`) — per TODO.md.
- Add tests for concurrent/repeated PDF corpus ingestion (idempotency, no interleaved writes) — per TODO.md.
- Implement `PostgresStore` (`apps/pdf_insight/stores/postgres_store.py`) when `psycopg` is added — currently a `NotImplementedError` skeleton.
- (Inferred) Continue building out Graph Builder toward the Cartograph entity-resolver spec (`docs/cartograph-brief.md`).
