# Implementation Plan — `pdf_insight` app

Status: **IMPLEMENTED** (four offline modes: the two tables-as-text modes, the
per-document SQL mode, and the whole-corpus query mode). `LLM_GETS_PDF_BYTES` is
wired as a guarded placeholder pending the gemini-only native-upload phase.

## Goal
A multi-mode PDF agent. The same coordinator answers questions over a PDF (or the
whole corpus) using one of five strategies ("modes"). Which mode runs is resolved
from a precedence chain (env → session → request) and, when nothing is pinned, by
LLM reasoning. The active mode is surfaced in the UI every turn.

## ADK principle driving the design
Deterministic work = Tools / custom agents; reasoning = `LlmAgent`. PDF parsing,
SQLite ingest, and SQL execution are deterministic tools. Only mode-selection
(when `auto`) and SQL generation use the model.

## The five modes (canonical names — use exactly these)
| Mode constant | Strategy | Determinism | Backend |
|---|---|---|---|
| `LLM_GETS_ALL_TABLES_AS_TEXT`  | pdfplumber → ALL tables rendered as text → model answers | deterministic extract; LLM answers | any (incl. mock) |
| `LLM_GETS_SOME_TABLES_AS_TEXT` | pdfplumber → SELECTED table(s) rendered as text → model | deterministic; LLM answers | any (incl. mock) |
| `LLM_MAKES_SQL_FROM_CHAT`      | pdfplumber tables → per-document SQLite → NL→SQL→execute | deterministic ingest/execute; LLM writes SQL | any for ingest/run; LLM for SQL |
| `LLM_QUERIES_CORPUS`           | whole-corpus DuckDB (every ingested report) → NL→SQL→execute | deterministic ingest/execute; LLM writes SQL | any for run; LLM for SQL |
| `LLM_GETS_PDF_BYTES`           | PDF bytes as multimodal `Part` → Gemini reads doc | model does all | gemini only |

**Scope.** The four non-bytes modes all run under `LLM_BACKEND=mock`.
`LLM_GETS_PDF_BYTES` remains a **later phase** (gemini-only): the `pdfbytes` mode
agent refuses it under any non-gemini backend with a clear error rather than
failing deep.

Sentinel `auto` = "no mode pinned, let the LLM router decide".

## Config resolution (precedence: request > session > env > default)
```python
# apps/pdf_insight/config.py
MODES = {"LLM_GETS_ALL_TABLES_AS_TEXT","LLM_GETS_SOME_TABLES_AS_TEXT",
         "LLM_MAKES_SQL_FROM_CHAT","LLM_QUERIES_CORPUS","LLM_GETS_PDF_BYTES","auto"}

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
  ingest.py          # ingest_pdf_everywhere — upload handler (SQLite replace + corpus append)
  stores/            # one SqlStore abstraction + one read-only guard, per engine
    __init__.py      # re-exports the stores + shared guard + corpus tools
    base.py          # SqlStore, validate_select, jsonable, run_select
    sqlite_store.py  # SQLiteStore.ingest_pdf + ingest_tables_to_sqlite, list_sql_schema, run_sql
    duckdb_store.py  # DuckDBStore.ingest_pdf (runtime corpus append) + the corpus store
    corpus_tools.py  # backend-neutral corpus tools + get_corpus_store (CORPUS_BACKEND)
    postgres_store.py # PostgresStore (skeleton; implements two methods to go live)
  modes/             # one module per PDF mode; build_dispatch() merges them
    __init__.py      # MODE_BUILDERS registry, build_dispatch()
    _common.py       # _user_text, _resolve_pdf_path, _parse_table_indices, _text_event
    pdfpart.py       # PdfPartTextAgent -> ALL_TABLES_AS_TEXT / SOME_TABLES_AS_TEXT
    text2sql.py      # SqlModeAgent + text2sql_agent -> SQL_FROM_TEXT
    corpus.py        # build_corpus_agent -> QUERY_CORPUS (whole-corpus DuckDB)
    pdfbytes.py      # PdfBytesAgent -> PDF_BYTES (gemini-only placeholder)
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
See `docs/plans/pdf_test_plan.md` for the test-quality rationale (coverage / mode
matrix / corpus correctness).
```
tests/pdf/
  conftest.py             # run_agent fixture + temp-dir storage isolation
  test_phase1_tools.py    # extract_tables / tables_as_text — pure, no LLM
  test_phase2_sql.py      # SQLite ingest + run_sql with hand-written SQL — pure, no LLM
  test_phase3_modes.py    # resolve_mode precedence (env/session/request)
  test_phase4_agent.py    # coordinator routing under mock; full 5-mode matrix + banner/state
  test_units.py           # index parsing, _resolve_pdf_path precedence, single/multi select
  test_errors.py          # malformed-PDF error branches (pdfpart / text2sql / extract_tables)
  test_duckdb_tools.py    # corpus guards + _jsonable + cross-week correctness vs golden
  test_golden_answers.py  # MockPdfLlm correctness across text / SQL / corpus modes
  test_upload_ingest.py   # ingest_pdf_everywhere: SQLite replace + corpus append (+ coordinator hook)
  test_coverage_gaps.py   # remaining defensive branches, each named by file:line
  test_live_modes.py      # live: each answering mode end-to-end on a real model
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
