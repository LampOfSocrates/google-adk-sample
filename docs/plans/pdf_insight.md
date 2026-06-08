# Implementation Plan — `pdf_insight` app

Status: **IMPLEMENTED** (first pass: the three offline modes). `LLM_GETS_PDF_BYTES`
is wired as a guarded placeholder pending the gemini-only native-upload phase.

## Goal
A multi-mode PDF agent. The same coordinator answers questions over a PDF using
one of four strategies ("modes"). Which mode runs is resolved from a precedence
chain (env → session → request) and, when nothing is pinned, by LLM reasoning.
The active mode is surfaced in the UI every turn.

## ADK principle driving the design
Deterministic work = Tools / custom agents; reasoning = `LlmAgent`. PDF parsing,
SQLite ingest, and SQL execution are deterministic tools. Only mode-selection
(when `auto`) and SQL generation use the model.

## The four modes (canonical names — use exactly these)
| Mode constant | Strategy | Determinism | Backend |
|---|---|---|---|
| `LLM_GETS_ALL_TABLES_AS_TEXT`  | pdfplumber → ALL tables rendered as text → model answers | deterministic extract; LLM answers | any (incl. mock) |
| `LLM_GETS_PDF_BYTES`           | PDF bytes as multimodal `Part` → Gemini reads doc | model does all | gemini only |
| `LLM_MAKES_SQL_FROM_CHAT`      | pdfplumber tables → local SQLite → NL→SQL→execute | deterministic ingest/execute; LLM writes SQL | any for ingest/run; LLM for SQL |
| `LLM_GETS_SOME_TABLES_AS_TEXT` | pdfplumber → SELECTED table(s) rendered as text → model | deterministic; LLM answers | any (incl. mock) |

**Scope for first pass:** `LLM_GETS_ALL_TABLES_AS_TEXT`, `LLM_MAKES_SQL_FROM_CHAT`,
`LLM_GETS_SOME_TABLES_AS_TEXT` (all run under `LLM_BACKEND=mock`).
`LLM_GETS_PDF_BYTES` is a **later phase** (gemini-only). The coordinator refuses
`LLM_GETS_PDF_BYTES` under mock with a clear error rather than failing deep.

Sentinel `auto` = "no mode pinned, let the LLM router decide".

## Config resolution (precedence: request > session > env > default)
```python
# apps/pdf_insight/config.py
MODES = {"LLM_GETS_ALL_TABLES_AS_TEXT","LLM_GETS_PDF_BYTES","LLM_MAKES_SQL_FROM_CHAT",
         "LLM_GETS_SOME_TABLES_AS_TEXT","auto"}

def resolve_mode(request_override, state, env) -> str:
    for candidate in (request_override,                 # 1. per-request (highest)
                      state.get("pdf_mode"),            # 2. per-session
                      env.get("PDF_MODE")):             # 3. env baseline
        if candidate and candidate in MODES:
            return candidate
    return "auto"                                       # 4. default -> reasoning
```
- **env**: `PDF_MODE=LLM_GETS_SOME_TABLES_AS_TEXT` in `.env` sets the baseline.
- **session**: a tool `set_pdf_mode(mode)` writes `state["pdf_mode"]` so the user
  can pin a mode mid-conversation; persists for the session.
- **request**: a `mode` key in the new message's **state-delta** when driving via
  `Runner` (programmatic), plus an optional inline directive `mode: <NAME>` parsed
  from the message text (convenience for `adk web`). Request override is per-turn
  only — it does not mutate session state.

## Showing the mode on the UI
Each turn the coordinator:
1. writes the resolved mode to `state["active_pdf_mode"]` (visible in the
   `adk web` State panel), and
2. prepends a one-line banner to its reply, e.g. `▸ mode: LLM_GETS_SOME_TABLES_AS_TEXT`.

## Coordinator: hybrid config/reasoning router (custom `BaseAgent`)
```python
class PdfInsightAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        mode = resolve_mode(request_override(ctx), ctx.session.state, os.environ)
        ctx.session.state["active_pdf_mode"] = mode
        yield banner_event(mode)                       # "▸ mode: ..."
        if mode == "LLM_GETS_PDF_BYTES" and is_mock():
            yield error_event("LLM_GETS_PDF_BYTES needs the gemini backend."); return
        target = self.router_agent if mode == "auto" else self.dispatch[mode]
        async for ev in target.run_async(ctx):
            yield ev
```
- `router_agent`: an `LlmAgent` whose instruction describes *when* each mode
  applies; it reasons and calls the right tool / sub-agent.
- `dispatch[mode]`: pins straight to one specialist, skipping the routing LLM.

(Alternative considered: single `LlmAgent` + `before_model_callback` short-circuit.
Rejected for clarity — the custom agent makes the config/reasoning split explicit.)

## Components & files
```
apps/pdf_insight/
  __init__.py        # from . import agent
  agent.py           # assembles root_agent from coordinator + modes registry
  coordinator.py     # PdfInsightAgent (base agent), router_agent
  config.py          # resolve_mode, MODES, request_override parsing
  tools.py           # extract_tables(select=...), tables_as_text, set_pdf_mode
  storage.py         # DSN resolution (state > env > default) for every backend
  stores/            # one SqlStore abstraction + one read-only guard, per engine
    base.py          # SqlStore, validate_select, jsonable, run_select
    sqlite_store.py  # SQLiteStore + ingest_tables_to_sqlite, list_sql_schema, run_sql
    duckdb_store.py  # DuckDBStore + list_corpus_schema, run_corpus_sql
    postgres_store.py # PostgresStore (skeleton; implements two methods to go live)
  modes/             # one module per PDF mode; build_dispatch() merges them
    __init__.py      # MODE_BUILDERS registry, build_dispatch()
    _common.py       # _user_text, _resolve_pdf_path, _parse_table_indices, _text_event
    pdf_template.py  # PdfTemplateTextAgent -> ALL_/SOME_TABLES_AS_TEXT
    text2sql.py      # SqlModeAgent + text2sql_agent -> SQL_FROM_TEXT
    corpus.py        # corpus_sql_agent -> QUERY_CORPUS (whole-corpus DuckDB)
    pdfbytes.py        # PdfBytesAgent -> PDF_BYTES (gemini-only placeholder)
shared/
  pdf.py             # pure pdfplumber helpers (reused by scripts/inspect_pdf.py)
```

### Deterministic tools (offline, unit-testable with no LLM)
- `extract_tables(pdf_path, select=None, strategy="lines") -> {status, count, tables}`
  - `select=None` → ALL tables (`LLM_GETS_ALL_TABLES_AS_TEXT`).
  - `select=[i, ...]` → chosen table indices (`LLM_GETS_SOME_TABLES_AS_TEXT`).
- `tables_as_text(tables) -> str` — renders selected tables to a flat text block
  (header + rows) for the model. Shared by both table-as-text modes.
- `set_pdf_mode(mode, tool_context) -> {status, message}` (writes session state)

### Text2SQL specialist (`LLM_MAKES_SQL_FROM_CHAT`)
```python
text2sql_agent = Agent(
    name="text2sql_agent", model=get_model(),
    instruction="Given the schema, write ONE SQLite SELECT, call run_sql, "
                "answer from rows. Never write/modify data.",
    tools=[list_sql_schema, run_sql])
```
- `ingest_tables_to_sqlite(pdf_path, db_path) -> {status, tables}` — one PDF table
  → one SQLite table (`t1`, `t2`, …). Header→column sanitizer with `col_N`
  fallback for merged/multi-row/ambiguous headers (see `scripts/inspect_pdf.py`
  warning). Ingest **once per session**; cache `db_path` in `state`.
- `run_sql` is the trust boundary: reject anything that isn't a single `SELECT`,
  cap returned rows.

## MockLlm additions (`shared/mock_llm.py`)
Most surface is deterministic tools → tested with **no model**. Narrow branches:
- router: when only PDF tools are available and mode is `auto`, return a default
  routing decision (e.g. call `extract_tables`).
- text2sql: when `run_sql`/`list_sql_schema` are available, return a canned
  `SELECT * FROM t1 LIMIT 5`, then summarize rows.
Mirror the existing tool-name heuristic in current `mock_llm.py`.

## Tests (phase-based, per existing convention)
```
tests/pdf/
  test_phase1_tools.py    # extract_tables / tables_as_text — pure, no LLM
  test_phase2_sql.py      # ingest + run_sql with hand-written SQL — pure, no LLM
  test_phase3_modes.py    # resolve_mode precedence (env/session/request)
  test_phase4_agent.py    # coordinator routing under mock; banner + active_pdf_mode
  eval_set_1.evalset.json # live (gemini) auto-routing + Text2SQL
```

## Dependencies
`pdfplumber` added explicitly to `requirements.txt`. `sqlite3` is stdlib — no new dep.

## Rollout phases
1. `shared/pdf.py` + `extract_tables`/`tables_as_text`; re-point `inspect_pdf.py`.
2. `config.py` + `resolve_mode` + `set_pdf_mode` + precedence tests.
3. SQLite ingest + `run_sql` (SELECT-only) + tests.
4. `PdfInsightAgent` (router + dispatch) + banner/active-mode + mock branches.
5. (later) `LLM_GETS_PDF_BYTES` native upload, gemini-only.

## Open questions / risks
- Request-level inline directive syntax (`mode: NAME`) — acceptable, or
  programmatic state-delta only?
- Header sanitization for badly-structured tables is best-effort; multi-row
  headers may need per-PDF tuning.
