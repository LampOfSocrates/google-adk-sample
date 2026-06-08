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

- [ ] **Charts in the Streamlit UI.**
  Auto-visualize SQL results: intercept the `run_corpus_sql` / `run_sql` tool
  results (already captured in the stream as `{columns, rows}`) and render a
  **table + an auto-chosen chart** in the chat — `st.line_chart` when there's a
  `report_date` column (time-series / week-over-week), `st.bar_chart` for
  category→measure. Zero new deps (native Vega-Lite); Plotly (`st.plotly_chart`)
  is a one-line upgrade for extra polish. Do NOT hand-roll HTML/SVG.
  Mirror the existing ```mermaid rendering pattern in `streamlit_app.py`.

## Simplification / tech debt

- [ ] **Simplify `scripts/pdf_creator.py`** (`# TODO(simplify)` at line 56).
  The synthetic-PDF generator has grown to 16 table builders across two render
  styles (ruled/borderless) plus a multi-flag CLI, while the suite uses only a
  slice (the committed all-ruled fixture). Trim the table catalog + CLI surface to
  what's actually exercised, and fold the ruled/borderless split into one path.

- [ ] **Simplify `shared/mock_pdf_llm.py`** (`# TODO(simplify)` at line 21).
  The NL → (measure, dimension, aggregation, filter) heuristic and its
  synonym/value vocab have grown hard to follow. Narrow it to the intents the
  golden tests actually assert, and table-drive the synonym matching, instead of
  expanding the hand-rolled parser.

## Known deferred (not TODO-marked, for visibility)

- [ ] **`PostgresStore` implementation** (`apps/pdf_insight/stores/postgres_store.py`).
  A working skeleton: `_connect_readonly`, `list_schema`, and `ingest_pdf` raise
  `NotImplementedError`. Implement when `psycopg` is added — open the DSN, `SET
  TRANSACTION READ ONLY`, and read/write the canonical `documents`/`pdf_tables`/`tNN`
  schema. The abstraction + `CORPUS_BACKEND=postgres` selector are already in place.
