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
