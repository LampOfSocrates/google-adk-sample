# google-adk-sample

A **holding repo for four independent ADK agents**. Each lives in its own folder
under `backend/`, is discovered by ADK on its own (`root_agent`), and can be run,
tested, and operated on its own. They share one thing: a single model layer
(`backend/shared/`) that runs on five interchangeable backends, so any agent works on
`mock` (offline) or a real LLM without code changes.

| # | Agent | Folder | One line |
|---|---|---|---|
| 1 | **Travel Planner** | `backend/travel_planner/` | Coordinator that routes to a weather sub-agent + a web-search agent-tool. |
| 2 | **PDF Insight** | `backend/pdf_insight/` | Multi-mode PDF Q&A: extract tables, ask in natural language, or run SQL over one PDF or a whole corpus. |
| 3 | **Text-to-Diagram** | `backend/text_to_diagram/` | Stateless pipeline: prose → knowledge-graph triads → mermaid diagram. |
| 4 | **Graph Builder** | `backend/graph_builder/` | Conversational, *accreting* knowledge graph — the entity resolver (the moat) of **Cartograph** (`docs/cartograph-brief.md`). |

The four agent sections below each cover **purpose · main folders · how to run**.
Cross-cutting material (backends, the shared layer, the Streamlit UI, testing,
and the decision log) follows.

---

## Backends & the three ways to run

Every agent binds its model from `shared/model.py`, selected by `LLM_BACKEND`
(see `.env.example`): `mock` / `gemini` / `openai` / `deepseek` / `bedrock`.
`mock` is the default — free, offline, no tokens. openai/deepseek/bedrock route
through ADK's LiteLlm wrapper (`pip install "google-adk[extensions]"`).

Any agent can be driven three ways:

- **`adk web`** — the ADK dev UI + debugger; auto-discovers every folder under
  `backend/`. Pick an agent from the dropdown. (`./local_run.sh` wraps this and sets
  `PYTHONPATH` so the agents' `backend.shared.*` imports resolve.)
- **FastAPI server + Streamlit client** — the Claude-style product chat UI
  (thinking blocks, live mermaid render, debug + agent-editor tabs, conversations)
  over all four agents. The server (`backend/server.py`) owns the ADK runners and
  streams turns over SSE; the client (`apps/pages/`) is a thin HTTP front-end.
  Start both: `./local_run.sh server` then `./local_run.sh ui` (or `./local_run.sh all`).
- **`python scripts/travel_planner/chat.py`** — a terminal REPL client for the
  server (hardwired to Travel Planner). Like the other agent-running scripts
  (`smoke.py`, `graph_eval.py`, `graph_demo.py`), it talks to the server over HTTP,
  so start `./local_run.sh server` first.

The split: agents + the model layer live in `backend/`; the FastAPI server
(`backend/server.py`) exposes them over a REST+SSE API (OpenAPI at `/docs`); the
Streamlit client in `apps/pages/` (`streamlit_app.py` + `api_client.py` + `render.py`)
holds no ADK and talks only HTTP. The UI event vocabulary lives in
`backend/shared/ui_stream.py`, adapted from ADK by `backend/shared/adk_ui_stream.py`.

---

## 1. Travel Planner — `backend/travel_planner/`  ✅ Done

**Purpose.** A textbook ADK coordinator that owns no domain logic and routes each
request to the right specialist. It's the reference for the two ways one agent can
reach another: **transfer** (sub-agent) vs **call** (AgentTool).

- A **weather sub-agent** (plain function tools `get_weather`, `set_preferred_units`)
  — the coordinator *transfers* control to it.
- A **web-search agent-tool** — the coordinator *calls* it and keeps control.
  `google_search` is Gemini-only, so every non-Gemini backend swaps in the offline
  `web_search` stand-in.

**Main folders / files.**
- `agent.py` — the whole topology: `weather_agent`, `search_agent`, and the
  `root_agent` coordinator. (This agent is small enough to be a single file.)
- `eval_set_1.evalset.json` — an `adk eval` set.
- Tests: `tests/travel_planner/` — `integration/` (`test_phase2` routing) and
  `e2e/` (`test_phase1` weather, `test_phase3` web search; both live).

**How to run.**
```bash
./local_run.sh server                             # start the agent server (once)
python scripts/travel_planner/chat.py             # terminal REPL client (this agent)
./local_run.sh                                    # adk web → pick "travel_planner"
pytest -m integration tests/travel_planner        # offline (mock)
pytest -m e2e        tests/travel_planner         # live (needs a key)
```

---

## 2. PDF Insight — `backend/pdf_insight/`  🟢 4 modes + upload

**Purpose.** Multi-mode PDF Q&A. One `PdfInsightAgent` (a custom `BaseAgent`
router) sends a question to one of five strategies; deterministic work (PDF parse,
SQL ingest/exec, mode-when-pinned) stays in code/tools, never in an LlmAgent — only
`auto`-routing and SQL generation call the model. Uploading a PDF makes it the
active document and **appends** it to a growing corpus. Full design:
`docs/plans/pdf_insight.md`; scope note: `backend/pdf_insight/CLAUDE.md`.

Modes (constants in `config.py`): `LLM_GETS_ALL_TABLES_AS_TEXT`,
`LLM_GETS_SOME_TABLES_AS_TEXT`, `LLM_MAKES_SQL_FROM_CHAT` (per-PDF SQLite),
`LLM_QUERIES_CORPUS` (whole-corpus DuckDB), `LLM_GETS_PDF_BYTES` (gemini-only
placeholder), and `auto` (the LLM router decides; corpus-aware — single-doc vs
cross-report).

**Main folders / files.**
- `agent.py` assembles `root_agent`; `coordinator.py` is the router (`PdfInsightAgent`).
- `modes/` — one module per strategy: `pdfpart.py`, `text2sql.py`, `pdfbytes.py`,
  `corpus.py`, merged by `modes.build_dispatch()`.
- `stores/` — SQL backends behind one `SqlStore` abstraction: `base.py` (the
  read-only `SELECT` guard), `sqlite_store.py`, `duckdb_store.py`, `postgres_store.py`.
- `ingest.py` (upload handler), `storage.py` (where each backend reads/writes),
  `config.py`, `tools.py`.
- Shared deps: `shared/pdf_extractor.py` (pdfplumber helpers), `shared/model.py`.
- Tests: `tests/pdf_insight/` — `unit/`, `integration/`, `e2e/` (live); the
  domain-aware offline mock `mock_pdf_llm.py` and the `conftest.py`/`samples/` live here too.

**How to run.**
```bash
./local_run.sh ui                          # Streamlit: PDF uploader + mode picker + "what's queryable"
./local_run.sh                             # adk web → pick "pdf_insight"
pytest -m unit        tests/pdf_insight     # fast pure-logic tests
pytest -m integration tests/pdf_insight     # agent/SQL/corpus under mock
pytest -m e2e         tests/pdf_insight     # live modes (needs a key)
```

The integration tests check **correctness offline, not just smoke**: the
domain-aware `mock_pdf_llm.py` actually computes answers from the parsed tables
(not a canned string), and those answers are asserted against committed golden
numbers in `tests/pdf_insight/fixtures/risk_report.golden.json`. So a wrong
extraction, SQL, or corpus query fails the run without spending a single token.

**Generate test PDFs & build the corpus.** Synthetic, multi-table risk reports
(Greeks across 4 regions, 16 tables/4 pages) — seeded → deterministic. `tests/pdf_insight/samples/*`
and `data/pdf_corpus.duckdb` are gitignored (regenerable).
```bash
# canonical fixture (committed) + golden answers, seed 42
python scripts/pdf_insight/pdf_creator.py --out tests/pdf_insight/fixtures/risk_report.pdf \
    --golden tests/pdf_insight/fixtures/risk_report.golden.json --pages 4 --tables 16 --seed 42

# dated weekly report(s) into tests/pdf_insight/samples/ (seed derived from the date)
python scripts/pdf_insight/weekly_report.py                 # this week's Friday
python scripts/pdf_insight/weekly_report.py --backfill 6    # seed the corpus: last 6 Fridays

# scan the samples; each PDF lands as its own tables, unioned per shape into views
python scripts/pdf_insight/pdf_to_duckdb.py --reset
python scripts/pdf_insight/pdf_to_duckdb.py --query "SELECT view_name, title, columns FROM families"
# then query a family view by its name (from the registry above), e.g.:
python scripts/pdf_insight/pdf_to_duckdb.py --query "SELECT report_date, region, vega_k FROM fam_00_<id> WHERE NOT is_total ORDER BY report_date"
```
On Windows PowerShell, invoke via the venv, e.g. `.venv\Scripts\python.exe scripts\pdf_insight\pdf_to_duckdb.py --reset`.

---

## 3. Text-to-Diagram — `backend/text_to_diagram/`  ✅ Done

**Purpose.** A stateless two-stage `SequentialAgent` that turns free text into a
mermaid knowledge-graph diagram, and deliberately contrasts the two kinds of ADK
agent:

- **Stage 1 — `triad_extractor`** (LlmAgent): extracts `(subject, predicate, object)`
  triads. Uses `output_schema` controlled generation on native backends; falls back
  to prompt-for-JSON + a `TriadParseAgent` validation stage on LiteLLM providers.
- **Stage 2 — `MermaidAgent`** (custom BaseAgent): renders triads to mermaid as pure
  string templating — zero tokens. The pattern for any mechanical step.

The accreting successor to this agent is Graph Builder (§4).

**Main folders / files.**
- `agent.py` — the pipeline (extractor, optional parser, mermaid builder, `root_agent`).
- `render.py` — the deterministic mermaid renderer.
- Tests: `tests/text_to_diagram/` — `unit/test_phase1_render.py`, `integration/test_phase2_pipeline.py`.

**How to run.**
```bash
./local_run.sh ui                            # Streamlit → pick "text_to_diagram" (mermaid renders live)
./local_run.sh                               # adk web → pick "text_to_diagram"
pytest -m unit        tests/text_to_diagram  # the pure render test
pytest -m integration tests/text_to_diagram  # the full pipeline under mock
```

---

## 4. Graph Builder — `backend/graph_builder/`  ✅ Resolver slice done

**Purpose.** Text-to-Diagram grown up. Where §3 is stateless (extract, render,
forget), Graph Builder **accretes**: a persistent graph lives in
`session.state["graph"]` and every turn folds new text into it. The hard part — the
moat — is **stage 2, the resolver**: deciding whether a freshly-named entity is an
*existing* node under a different surface name (`auth-service` = `AuthN` = `Identity
Provider`) or a genuinely new one, with confidence + reason. This is the first code
slice of **Cartograph** (`docs/cartograph-brief.md`); grounding is deliberately
**faked** (chat-only, soft provenance-tagged claims) to isolate resolution.

Three stages: `mention_extractor` (LlmAgent) → `entity_resolver` (LlmAgent, the
moat) → `grapher` (deterministic BaseAgent that applies resolutions, accretes
claims with provenance, and renders the graph + a resolution log).

> On `mock` the resolver can't reason → it returns all-new (wrong-split), which is
> exactly the failure a real `LLM_BACKEND=gemini` run must fix.

**Main folders / files.**
- `agent.py` — the three stages + `root_agent`.
- `render.py` — graph → mermaid renderer.
- `evals.py` — the offline scorer (correct merges vs over-merges).
- Tests: `tests/graph_builder/` — `unit/test_graph_evals.py`, `integration/test_graph_builder.py`.

**How to run.**
```bash
./local_run.sh server                                          # start the agent server (once)
python scripts/graph_builder/graph_demo.py                     # adversarial demo (mock → WRONG-SPLIT)
LLM_BACKEND=gemini python scripts/graph_builder/graph_demo.py  # real resolver → should MERGE
LLM_BACKEND=gemini python scripts/graph_builder/graph_eval.py  # resolver scorecard over the eval set
./local_run.sh ui                              # Streamlit client → pick "graph_builder"
pytest -m unit        tests/graph_builder      # the offline scorer test
pytest -m integration tests/graph_builder      # the resolver pipeline under mock
```

---

## Testing

Tests are organized on **two axes** — by usecase (folder) and by kind (marker):

```
tests/<usecase>/<unit|integration|e2e>/…       # e.g. tests/pdf_insight/integration/
```

- **unit** — pure logic, no agent runner. **integration** — agent + tools/stores
  via `InMemoryRunner` on the **mock** backend (offline). **e2e** — hits a **real**
  LLM (equivalent to the `live` marker).
- Markers are **auto-applied from the folder** (a `pytest_collection_modifyitems`
  hook in the root `conftest.py`), and registered in `pytest.ini`. `e2e ⇔ live`, so
  `pytest -m live` and `pytest -m e2e` select the same tests.

Select by **either axis, or both**:

```bash
pytest                                   # everything (e2e/live tests hit a real LLM)
pytest -m "not e2e"                      # fast, offline — MockLlm, no API/quota (CI default)
pytest -m unit                           # all unit tests, every usecase
pytest tests/pdf_insight                 # one usecase, all kinds
pytest -m integration tests/pdf_insight  # one usecase, one kind  ← combine the axes
```

**Live tests against a real LLM.** Pick the backend with `--backend` — no env-var
juggling, and it works across the whole suite (the offline tests stay on mock):

```bash
pytest -m live --backend deepseek    # DeepSeek  (needs DEEPSEEK_KEY in .env)
pytest -m live --backend openai      # OpenAI    (needs OPENAI_KEY)
pytest -m live --backend gemini      # Gemini    (needs GOOGLE_API_KEY; this is the default)
```

On Windows PowerShell, invoke pytest via the venv:
`.venv\Scripts\python.exe -m pytest -m live --backend deepseek -v`

How it works: the model **object** binds lazily — per turn, via `LazyModel`
(`backend/shared/model.py`) — so importing an agent never freezes the backend. But an agent's
**structure** is still chosen at build time from the active backend: which tools to
attach and schema-vs-prompt (e.g. the Gemini-only `google_search` / native PDF gate).
That's why the offline modules force `LLM_BACKEND=mock` at import, and why `conftest.py`
freezes the `--backend` choice (in `LIVE_BACKEND`) before collection so each live test
module can re-apply it just before importing its agent — keeping a live run immune to
those offline modules. `scripts/shared/smoke.py <backend>` drives all agents through the
server on a given backend and writes a `tests/smoke-results/<backend>.txt` scorecard.

> Note: Travel Planner's `test_phase3` (web search) asserts on a `google_search`
> result, which only runs on Gemini; other backends get the offline `web_search`
> stand-in. Use `test_phase1`/`test_phase2` as the clean cross-backend smoke tests.

---

## DecisionLog

A running record of UI/architecture decisions, what they buy us, and what they cost —
so future-us (and new contributors) know *why*, not just *what*. Newest first.

### 2026-06-08 — pdf_insight fully wired into the product UI (closes 06-05 "owed")

**Decision realized.** The two debts the 2026-06-05 "Streamlit product UI is built"
entry left owed are now paid: the files are **committed**, and **pdf_insight is wired
into the `APPS` map** with its own pane — sidebar PDF uploader (ingests on upload),
per-turn mode picker, a top-level Query (whole-corpus) toggle, and a "what's
queryable" panel (corpus coverage + table registry). `auto` mode is now
corpus-aware: it routes single-document questions to the active PDF and
cross-report / over-time questions to the corpus DB.

**Still owed.** The pane uses the generic chat surface — no in-document **PDF viewer
+ citation highlight** yet (the Streamlit decision below flags `streamlit-pdf-viewer`
for this). `LLM_GETS_PDF_BYTES` remains a gemini-only placeholder.

### 2026-06-05 — Cartograph: build the **resolver (L1) first**, not the survey (L0)

**Decision.** The Cartograph brief says "build L0 first" (objective grounding is the
fastest standalone win). We **inverted that** for the first code slice: `graph_builder`
implements the **entity resolver — the moat — over chat text, with grounding deliberately
faked** (every claim is soft, provenance-tagged, no verified anchor).

**Why.** L0 is objective but mechanical; the resolver is the hard, differentiating part
(wrong-split vs over-merge). Faking grounding isolates resolution so we can measure it now
(`backend/graph_builder/evals.py` scorer; `scripts/graph_builder/graph_eval.py` scorecard) without first
building the whole scanner crew. On `mock` the resolver can't reason → all-new (wrong-split)
→ which is exactly the failure the real `gemini` run must fix.

**Cost / what's still owed.** L0 stays unbuilt: the golden fixture
(`tests/graph_builder/fixtures/sample_repo1/`) and the `l0-survey-agent.md` spec are ready, but no survey
agent/tools consume them yet. Until L0 lands, the graph has no clickable ground truth — it's
a resolution demo, not the full product.

### 2026-06-05 — Streamlit product UI is built (chat pane)

**Decision realized.** The "Streamlit for now" decision below is now **implemented**:
`apps/pages/streamlit_app.py` is a Claude-style chat over the ADK apps — collapsible Thinking block
(tool calls + reasoning), token streaming, live mermaid render, and a per-turn Debug tab.
All ADK contact funnels through `shared/ui_stream.py` (raw events → flat `UIEvent`s);
`apps/pages/ui_debug.py` backs the debug tab.

**Owed.** Files are not yet committed, and **pdf_insight is not wired into the `APPS` map** —
it needs its own pane (PDF viewer + mode banner), not just the generic chat surface.

### 2026-06-05 — UI architecture: one FastAPI server, product UIs as siblings of `adk web`

**Decision.** Don't replace `adk web`; **invert it.** `get_fast_api_app(agents_dir=...,
web=True)` returns a plain `FastAPI` app that already mounts the dev UI *and* every debug
endpoint (`/run_sse`, sessions, events, traces, eval, artifacts). We add our own product
routes to that same app, so each use-case gets a bespoke UI **as a sibling** of the dev UI,
sharing the same agents and sessions.

```
agents/                 # one folder per agent — dev UI auto-discovers all
backend/  -> get_fast_api_app(web=True)
  /dev-ui               # full adk web debugger, untouched
  /chat /pdf /diagram   # our product UIs, same /run_sse + session endpoints
```

| Good | Bad / Limits |
|---|---|
| Product UIs and the debugger share **one server, one agent backend, one session store**. | The Angular dev UI is a **sealed bundle** — can't inject child views/tabs *inside* it. We sit beside it, not within it. |
| Because sessions are shared, a "Debug" deep-link opens the dev UI on the **exact session a user just had** — debug real conversations, not a sandbox. | Branding hooks (`logo_text`, `logo_image_url`, `url_prefix`) only *theme* the dev UI; they don't reshape it into a product surface. |
| New agent = new `agents/` folder (auto-discovered) + optional product route. Debug layer is free. | Product panes are still **our code** — `adk web` renders no PDF viewer or diagram for us. |

### 2026-06-05 — Frontend stack: Streamlit (for now)

**Decision.** Build the three product panes in **Streamlit**, mounted alongside the dev UI.
Chosen for speed and staying in Python (same language as the agents). Revisit if/when a
pane needs interactions Streamlit's execution model can't give.

**Structural ceiling (applies to every pane).** Streamlit reruns the whole script top-to-
bottom on each interaction. `st.fragment` (partial reruns) and `st.write_stream` (token
streaming) blunt this, but it's the ceiling — it bites *stateful* surfaces (scroll
position, partial edits, streaming), not stateless ones.

Per use-case:

| Use-case | Good | Bad / Limits | Verdict |
|---|---|---|---|
| **Travel Planning Chat** | Chat fits Streamlit natively; `st.write_stream` gives token streaming from `/run_sse`. | Rerun model needs `st.session_state` to hold history; care needed around streaming + reruns. | ✅ Good fit |
| **PDF Insight** | `streamlit-pdf-viewer` supports page nav **and annotation overlays** — highlight the exact region a citation came from. | Iframe-based community component; **large PDFs (100+ pp) sluggish**; reruns can reset scroll / flicker. | ✅ Good enough; watch big docs |
| **Text-to-Diagram (render-only)** | `st.graphviz_chart()` is **native and clean**; Mermaid via `streamlit-mermaid`. | Static rendered image in an iframe. | ✅ Fine if the diagram is a viewed/downloaded artifact |
| **Text-to-Diagram (editable canvas)** | — | No node dragging / click-to-edit / live re-layout. **Hits the ceiling.** | ❌ Needs React |
| **Plotter** | Streamlit's home turf — `st.plotly_chart` gives full zoom/pan/hover/selection. | None material. | ✅ Excellent |

**Revisit triggers (move a pane to React/Next when):**
- Text-to-Diagram must be an **interactive editor**, not a generated-diagram display.
- PDF Insight needs smooth handling of **large documents** or rich in-document interaction.
- Streaming/stateful flicker from the rerun model becomes a UX blocker users notice.

Net: Streamlit gives a genuinely good UI for the plotter, PDF view, and diagram *render*,
and a solid demo of all three. The only hard miss is an *editable* diagram canvas.
