# pdf_insight — scope note

Multi-mode PDF Q&A agent: one `PdfCoordinator` (custom `BaseAgent`) routes a
question to one of four strategies. Full design: `docs/plans/pdf_insight.md`.

## Boundary (what to touch)
- This app = `apps/pdf_insight/` (agent, config, tools, sql_tools) + `tests/pdf/`.
- Only shared deps: `shared/pdf.py` (pdfplumber helpers), `shared/model.py`
  (backend/get_model), and `shared/mock_llm.py` in tests.
- **Ignore** the sibling apps (`text_to_diagram`, `travel_planner`), `shared/schemas.py`,
  and everything under `docs/cartograph*` / `tests/fixtures/fake_system/` — none are
  dependencies of this app.

## Run / test
- Tests (offline, no API key): `pytest tests/pdf` — runs under `LLM_BACKEND=mock`.
- Live: set `LLM_BACKEND=gemini` (+ key). `adk web` discovers `root_agent`.

## Invariant (don't break)
Deterministic work (PDF parse, SQLite ingest, SQL exec, mode-when-pinned) lives in
tools / custom `BaseAgent`s — **never** an `LlmAgent`. Only `auto`-mode routing and
SQL generation call the model.

## Mode names — use these constants exactly (see `config.py`)
`LLM_GETS_ALL_TABLES_AS_TEXT` · `LLM_GETS_SOME_TABLES_AS_TEXT` ·
`LLM_GIVES_SQL_FROM_TEXT` · `LLM_GETS_PDF_BYTES` (gemini-only, placeholder) ·
`auto` (sentinel: let the LLM router decide).

`run_sql` is the trust boundary: single read-only `SELECT` only.
