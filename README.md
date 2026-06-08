# google-adk-sample

A multi-agent ADK sample: a small suite of agent-backed apps under `apps/`, sharing
one model layer (`shared/`) that runs on five interchangeable backends. The newest
app, **Graph Builder**, is the first slice of **Cartograph** — a self-building
knowledge graph of a system (see `docs/cartograph-brief.md`).

Run the agents locally three ways:
- `streamlit run streamlit_app.py` — Claude-style product chat UI (thinking blocks,
  live mermaid render, per-turn debug tab) over the apps.
- `adk web` — the ADK dev UI + debugger (auto-discovers each `apps/` folder).
- `python scripts/chat.py` — terminal REPL.

See `.env.example` for backend selection (`mock` / `gemini` / `openai` / `deepseek`
/ `bedrock`); `mock` is the default — free, offline, no tokens.

---

## Feature status (2026-06-05)

| Feature | State | Notes |
|---|---|---|
| **Travel Planner** (`apps/travel_planner`) | ✅ Done | Coordinator → weather sub-agent + web-search agent-tool. `google_search` on gemini; offline `web_search` stand-in elsewhere. |
| **PDF Insight** (`apps/pdf_insight`) | 🟢 4 modes + upload | `ALL_/SOME_TABLES_AS_TEXT`, `LLM_MAKES_SQL_FROM_CHAT` (per-PDF SQLite), `LLM_QUERIES_CORPUS` (whole-corpus DuckDB) done; `LLM_GETS_PDF_BYTES` a guarded gemini placeholder. **Upload ingests into both backends** (SQLite replace + corpus append) via one `SqlStore` abstraction (SQLite/DuckDB/Postgres-ready). |
| **Text-to-Diagram** (`apps/text_to_diagram`) | ✅ Done | Stateless: prose → triads → mermaid. The accreting successor is Graph Builder. |
| **Graph Builder** (`apps/graph_builder`) | ✅ Resolver slice done | Conversational, *accreting* KG. Stage-2 **resolver** (the moat: attach-vs-new with provenance) implemented; eval set + offline scorer + demo/eval scripts in place. **Grounding is faked** (chat-only, soft claims) to isolate resolution. |
| **Backends** (5) | ✅ Done | `mock`/`gemini`/`openai`/`deepseek`/`bedrock`, all smoke-tested 3/3 (`tests/smoke-results/`). LiteLLM providers use the prompt+parse fallback (`supports_output_schema()`). |
| **Streamlit product UI** (`streamlit_app.py`) | ✅ pdf_insight wired in | Chat over pdf_insight / travel_planner / graph_builder / text_to_diagram. **pdf_insight: sidebar PDF uploader (ingests on upload), per-turn mode picker, and a "what's queryable" panel** (corpus coverage + table registry). |
| **Cartograph L0** (survey/grounding agent) | 📋 Spec + fixture only | `docs/l0-survey-agent.md` spec; golden fixture `tests/fixtures/fake_system/` ready; vendor script for Online Boutique. **No survey agent/tools/test built yet** — the resolver-first pivot deferred it. |

---

## Testing

```bash
pytest -m "not live"        # fast, offline — runs on MockLlm, no API/quota (default for CI)
pytest                      # everything (live tests hit a real LLM)
pytest -m live              # only the live tests, on Gemini (default backend)
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

How it works: the agent binds its model **at import**, so `conftest.py` freezes the
`--backend` choice (in `LIVE_BACKEND`) before collection, and each live test module
re-applies it just before importing its agent. That's why a live run is immune to the
offline modules that force `LLM_BACKEND=mock` at import. openai/deepseek/bedrock route
through ADK's LiteLlm wrapper — `pip install "google-adk[extensions]"`.

> Note: `test_phase3` (web search) asserts on a `google_search` result, which only runs
> on Gemini; other backends get the offline `web_search` stand-in. Use `test_phase1`/
> `test_phase2` as the clean cross-backend smoke tests.

---

## PDF test data & corpus

Synthetic, multi-table PDFs for testing `pdf_insight` — a multi-region derivatives
desk risk report (Greeks: delta/gamma/vega/theta/rho), 16 aggregation tables across
4 pages. Built with reportlab; parsed back with pdfplumber. Seeded → deterministic.

**Generate the PDFs.** One canonical fixture, or a dated weekly corpus:

```bash
# canonical fixture (committed) + golden answers, all 16 tables, seed 42
python scripts/pdf_creator.py --out tests/fixtures/risk_report.pdf \
    --golden tests/fixtures/risk_report.golden.json --pages 4 --tables 16 --seed 42

# dated weekly report(s) into tests/pdf/samples/ (seed derived from the date)
python scripts/weekly_report.py                 # this week's Friday
python scripts/weekly_report.py --backfill 6    # seed the corpus: last 6 Fridays
```

**Parse them into tables (→ DuckDB).** Scan the corpus, extract every table, and land
it in `data/pdf_corpus.duckdb` (each logical table accumulates across weeks, keyed by
`report_date`):

```bash
python scripts/pdf_to_duckdb.py --reset                       # (re)build the DB
python scripts/pdf_to_duckdb.py --query "SELECT table_index, title FROM pdf_tables"
python scripts/pdf_to_duckdb.py --query \
    "SELECT report_date, vega_k FROM t00 WHERE region='Americas' AND NOT is_total ORDER BY report_date"
```

`tests/pdf/samples/*` and `data/pdf_corpus.duckdb` are gitignored (regenerable) — run
the two scripts above whenever you want more data. On Windows PowerShell, invoke via
the venv, e.g. `.venv\Scripts\python.exe scripts\pdf_to_duckdb.py --reset`.

---

## DecisionLog

A running record of UI/architecture decisions, what they buy us, and what they cost —
so future-us (and new contributors) know *why*, not just *what*. Newest first.

### 2026-06-05 — Cartograph: build the **resolver (L1) first**, not the survey (L0)

**Decision.** The Cartograph brief says "build L0 first" (objective grounding is the
fastest standalone win). We **inverted that** for the first code slice: `graph_builder`
implements the **entity resolver — the moat — over chat text, with grounding deliberately
faked** (every claim is soft, provenance-tagged, no verified anchor).

**Why.** L0 is objective but mechanical; the resolver is the hard, differentiating part
(wrong-split vs over-merge). Faking grounding isolates resolution so we can measure it now
(`apps/graph_builder/evals.py` scorer; `scripts/graph_eval.py` scorecard) without first
building the whole scanner crew. On `mock` the resolver can't reason → all-new (wrong-split)
→ which is exactly the failure the real `gemini` run must fix.

**Cost / what's still owed.** L0 stays unbuilt: the golden fixture
(`tests/fixtures/fake_system/`) and the `l0-survey-agent.md` spec are ready, but no survey
agent/tools consume them yet. Until L0 lands, the graph has no clickable ground truth — it's
a resolution demo, not the full product.

### 2026-06-05 — Streamlit product UI is built (chat pane)

**Decision realized.** The "Streamlit for now" decision below is now **implemented**:
`streamlit_app.py` is a Claude-style chat over the ADK apps — collapsible Thinking block
(tool calls + reasoning), token streaming, live mermaid render, and a per-turn Debug tab.
All ADK contact funnels through `shared/ui_stream.py` (raw events → flat `UIEvent`s);
`shared/debug.py` backs the debug tab.

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
