"""Coordinator end-to-end under MockLlm: routing, banner, and session state.

Drives the whole agent graph through `converse`, asserting the coordinator resolves
the right mode, dispatches to the matching sub-agent, and writes the expected state
(active_pdf_mode, published table text, db_path). Imports `root_agent` directly: its
model binds lazily (shared.model.LazyModel resolves LLM_BACKEND per turn), so the
import can't freeze the backend and the autouse `_backend_env` fixture pinning mock
at run time is enough. Modes are pinned per request with a `mode:` directive, so no
initial session state is needed.
"""
from backend.pdf_insight import config
from backend.pdf_insight.agent import root_agent


async def test_auto_mode_routes_through_extract_tables(converse):
    """With no mode pinned, the coordinator resolves AUTO and the mock router takes
    the extract_tables -> summarize path, so the rendered table text reaches the user."""
    answers, state = await converse(root_agent, ["What is in this statement?"])
    assert state["active_pdf_mode"] == config.AUTO
    # mock router -> extract_tables -> summarize returns the rendered table text
    assert "Table 0" in answers[-1]


async def test_pinned_all_tables_publishes_table_text(converse):
    """Pinning ALL_TABLES_AS_TEXT runs the deterministic stage that publishes every
    table as text into state for the answerer to read."""
    answers, state = await converse(
        root_agent,
        [f"mode: {config.ALL_TABLES_AS_TEXT} summarize the holdings"],
    )
    assert state["active_pdf_mode"] == config.ALL_TABLES_AS_TEXT
    # the deterministic stage published every table as text for the answerer
    assert "Table 0" in state["pdf_tables_text"]


async def test_pinned_sql_mode_ingests_and_delegates(converse):
    """Pinning SQL_FROM_TEXT ingests the PDF (db_path appears in state) and delegates
    to the Text2SQL agent, which produces an answer."""
    answers, state = await converse(
        root_agent,
        [f"mode: {config.SQL_FROM_TEXT} how many holdings are there?"],
    )
    assert state["active_pdf_mode"] == config.SQL_FROM_TEXT
    assert "db_path" in state          # ingestion ran
    assert answers[-1]                 # text2sql produced some answer


async def test_pinned_some_tables_publishes_table_text(converse):
    """Pinning SOME_TABLES_AS_TEXT with `table 0` publishes that table's text into
    state — the SOME path's happy case."""
    answers, state = await converse(
        root_agent,
        [f"mode: {config.SOME_TABLES_AS_TEXT} table 0 holdings"],
    )
    assert state["active_pdf_mode"] == config.SOME_TABLES_AS_TEXT
    assert "Table 0" in state["pdf_tables_text"]


async def test_some_tables_override_selects_only_that_table(converse):
    """The 'some' distinction: a per-request `table N` override must publish ONLY
    that table to the answerer — not every table (that would be the 'all' mode)."""
    answers, state = await converse(
        root_agent,
        [f"mode: {config.SOME_TABLES_AS_TEXT} table 2 holdings"],
    )
    text = state["pdf_tables_text"]
    assert "Table 2" in text          # the requested table is present...
    assert "Table 0" not in text      # ...and the others are excluded (true subset)
    assert "Table 1" not in text


async def test_pinned_corpus_mode_routes(converse):
    """The coordinator dispatches a QUERY_CORPUS pin to the corpus agent without
    crashing — routing only; answer correctness lives in test_golden_answers.py."""
    # Routing-deep only: under the plain MockLlm the corpus agent just replies; the
    # point is the coordinator dispatches to QUERY_CORPUS without crashing.
    # (Answer correctness for this mode lives in test_golden_answers.py / MockPdfLlm.)
    answers, state = await converse(
        root_agent,
        [f"mode: {config.QUERY_CORPUS} total vega by region"],
    )
    assert state["active_pdf_mode"] == config.QUERY_CORPUS
    assert answers[-1]  # produced a reply


async def test_sql_mode_reingests_when_pdf_changes(run_agent, monkeypatch):
    """Regression: a mid-session PDF switch must re-ingest, not reuse the stale db.

    State claims an earlier PDF was already ingested (db_path + db_source_pdf set);
    the active PDF is now the fixture. The old gate (`if not db_path`) skipped
    ingestion and answered from the previous document.
    """
    from backend.pdf_insight.modes import text2sql as sqlmod

    calls = []
    real = sqlmod.ingest_tables_to_sqlite
    monkeypatch.setattr(
        sqlmod, "ingest_tables_to_sqlite",
        lambda pdf_path, db_path: (calls.append(pdf_path), real(pdf_path, db_path))[1],
    )

    fixture = "tests/pdf_insight/fixtures/risk_report.pdf"
    agent = sqlmod.build()[config.SQL_FROM_TEXT]
    await run_agent(agent, "how many rows?", {
        "pdf_path": fixture,
        "db_path": "/tmp/stale_old.pdf.sqlite",
        "db_source_pdf": "old.pdf",
    })
    assert calls == [fixture]  # re-ingested the new PDF, didn't reuse the stale db


async def test_native_bytes_mode_runs_on_any_backend(converse):
    """Native PDF_BYTES now answers on any document-capable backend, not just gemini.

    Under mock the model ignores the attached bytes and returns its offline
    placeholder, but the point is the mode dispatches and answers cleanly — no
    'needs the gemini backend' refusal and no crash."""
    answers, state = await converse(
        root_agent,
        [f"mode: {config.PDF_BYTES} read the whole document"],
    )
    assert state["active_pdf_mode"] == config.PDF_BYTES
    assert answers[-1]  # produced an answer rather than refusing/erroring
    assert "needs the gemini backend" not in answers[-1].lower()
