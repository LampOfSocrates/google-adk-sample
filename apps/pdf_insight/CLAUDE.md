# pdf_insight — scope note

Multi-mode PDF Q&A agent: one `PdfCoordinator` (custom `BaseAgent`) routes a
question to one of four strategies. Full design: `docs/plans/pdf_insight.md`.

## Boundary (what to touch)
- This app = `apps/pdf_insight/` + `tests/pdf/`. Layout: `agent.py` assembles
  `root_agent`; `coordinator.py` = base router (`PdfCoordinator`); `modes/` = one
  module per mode (`tables.py`, `sql.py`, `native.py`, `stash.py`) registered via
  `modes.build_dispatch()`; `stores/` = the SQL backends behind one `SqlStore`
  abstraction (`base.py` guard + `run_select`; `sqlite_store.py`, `duckdb_store.py`,
  `postgres_store.py`); `storage.py` resolves where each backend reads/writes;
  `config.py`, `tools.py` round it out.
- Only shared deps: `shared/pdf.py` (pdfplumber helpers), `shared/model.py`
  (backend/get_model), and `shared/mock_llm.py` in tests.
- Stash mode reads `data/pdf_stash.duckdb`, built offline by
  `scripts/pdf_to_duckdb.py` from the `tests/pdf/samples/` report stash.
- **Ignore** the sibling apps (`text_to_diagram`, `travel_planner`), `shared/schemas.py`,
  and everything under `docs/cartograph*` / `tests/fixtures/fake_system/` — none are
  dependencies of this app.

## Run / test
- Tests (offline, no API key): `pytest -m "not live" tests/pdf` — runs under `LLM_BACKEND=mock`.
- Live smoke (one per answering mode, needs a key): `pytest -m live tests/pdf`.
- Live app: set `LLM_BACKEND=gemini` (+ key). `adk web` discovers `root_agent`.

## Invariant (don't break)
Deterministic work (PDF parse, SQLite ingest, SQL exec, mode-when-pinned) lives in
tools / custom `BaseAgent`s — **never** an `LlmAgent`. Only `auto`-mode routing and
SQL generation call the model.

## Mode names — use these constants exactly (see `config.py`)
`LLM_GETS_ALL_TABLES_AS_TEXT` · `LLM_GETS_SOME_TABLES_AS_TEXT` ·
`LLM_MAKES_SQL_FROM_CHAT` · `LLM_GETS_PDF_BYTES` (gemini-only, placeholder) ·
`LLM_QUERIES_STASH` (whole-stash DuckDB across all reports) ·
`auto` (sentinel: let the LLM router decide).

Trust boundary: one shared guard in `stores/base.py` (`validate_select` +
read-only `run_select`) — `run_sql` (SQLite) and `run_stash_sql` (DuckDB) are thin
adapters over it. A single read-only `SELECT`/`WITH` only. Postgres slots in by
implementing two methods on `SqlStore` (`postgres_store.py`).
