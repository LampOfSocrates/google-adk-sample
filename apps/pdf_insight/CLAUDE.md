# pdf_insight — scope note

Multi-mode PDF Q&A agent: one `PdfInsightAgent` (custom `BaseAgent`) routes a
question to one of five strategies. Full design: `docs/plans/pdf_insight.md`.

## Boundary (what to touch)
- This app = `apps/pdf_insight/` + `tests/pdf_insight/`. Layout: `agent.py` assembles
  `root_agent`; `coordinator.py` = base router (`PdfInsightAgent`); `modes/` = one
  module per mode (`pdfpart.py`, `text2sql.py`, `pdfbytes.py`, `corpus.py`) registered via
  `modes.build_dispatch()`; `stores/` = the SQL backends behind one `SqlStore`
  abstraction (`base.py` guard + `run_select`; `sqlite_store.py`, `duckdb_store.py`,
  `postgres_store.py`); `storage.py` resolves where each backend reads/writes;
  `ingest.py` = the upload handler; `config.py`, `tools.py` round it out.
- Only shared deps: `shared/pdf_extractor.py` (pdfplumber helpers), `shared/model.py`
  (backend/get_model), and `shared/mock_llm.py` in tests.
- **Upload = a new active PDF.** The coordinator calls `ingest.ingest_pdf_everywhere`
  once per new PDF: (re)ingest into the per-document SQLite (latest-PDF-only) AND
  **append** to the corpus. The Streamlit UI's PDF uploader uses the same handler.
- Corpus = `data/pdf_corpus.duckdb` (or `PDF_CORPUS_DB`). Written by
  `DuckDBStore.ingest_pdf` at runtime AND by the offline batch `scripts/pdf_insight/pdf_to_duckdb.py`
  (same code path); queried `read_only`.
- **Ignore** the sibling apps (`text_to_diagram`, `travel_planner`), `shared/schemas.py`,
  and everything under `docs/cartograph*` / `tests/fixtures/reference_system/` — none are
  dependencies of this app.

## Run / test
- Tests (offline, no API key): `pytest -m "not live" tests/pdf_insight` — runs under `LLM_BACKEND=mock`.
- Live smoke (one per answering mode, needs a key): `pytest -m live tests/pdf_insight`.
- Live app: set `LLM_BACKEND=gemini` (+ key). `adk web` discovers `root_agent`.

## Invariant (don't break)
Deterministic work (PDF parse, SQLite + corpus ingest, SQL exec, mode-when-pinned)
lives in tools / stores / custom `BaseAgent`s — **never** an `LlmAgent`. Only
`auto`-mode routing and SQL generation call the model.

## Mode names — use these constants exactly (see `config.py`)
`LLM_GETS_ALL_TABLES_AS_TEXT` · `LLM_GETS_SOME_TABLES_AS_TEXT` ·
`LLM_MAKES_SQL_FROM_CHAT` · `LLM_GETS_PDF_BYTES` (gemini-only, placeholder) ·
`LLM_QUERIES_CORPUS` (whole-corpus DuckDB across all reports) ·
`auto` (sentinel: let the LLM router decide).

Trust boundary: one shared guard in `stores/base.py` (`validate_select` +
read-only `run_select`) — `run_sql` (SQLite) and `run_corpus_sql` (DuckDB) are thin
adapters over it. A single read-only `SELECT`/`WITH` only. Postgres slots in by
implementing two methods on `SqlStore` (`postgres_store.py`).
