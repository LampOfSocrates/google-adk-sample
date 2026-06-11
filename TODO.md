# TODO

Tracked follow-ups (not blocking; each is self-contained).

## pdf_insight

- [ ] **`auto` mode → corpus routing works offline (mock).**
  Today `auto` under `LLM_BACKEND=mock` always routes to the single active PDF
  (`extract_tables`) — the generic `MockLlm` can't *reason* "this is a
  cross-report/trend question → use the corpus", so it just calls the first tool.
  Live `gemini` routes correctly via the updated `build_router` instruction
  (`coordinator.py`), and **explicit corpus mode works regardless**.
  Fix: add a small corpus-routing heuristic to the offline mock — when the router's
  corpus tools (`list_corpus_schema`/`run_corpus_sql`) are available and the
  question is trend-like ("trend / over time / since / each week / all reports /
  week-over-week"), call `list_corpus_schema` then `run_corpus_sql` instead of
  `extract_tables`. Likely in `shared/mock_llm.py` (keep `extract_tables` the
  default so existing routing tests hold).

- [ ] **Concurrency/idempotency of corpus append under repeated/parallel uploads.**
  `ingest_pdf_everywhere` appends to the corpus DuckDB idempotently per `report_date`,
  but this isn't explicitly tested under repeated or parallel uploads. Add tests that
  re-ingest the same report (assert no duplicate rows) and that concurrent uploads of
  distinct reports don't corrupt the corpus or interleave partial writes.

## Known deferred (not TODO-marked, for visibility)

- [ ] **`PostgresStore` implementation** (`apps/pdf_insight/stores/postgres_store.py`).
  A working skeleton: `_connect_readonly`, `list_schema`, and `ingest_pdf` raise
  `NotImplementedError`. Implement when `psycopg` is added — open the DSN, `SET
  TRANSACTION READ ONLY`, and read/write the canonical `documents`/`pdf_tables`/`tNN`
  schema. The abstraction + `CORPUS_BACKEND=postgres` selector are already in place.
