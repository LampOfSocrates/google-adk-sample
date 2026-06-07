"""Phase 4 — the coordinator end-to-end under MockLlm: routing, banner, state.

Forces the offline backend before importing the agent so every LlmAgent in the
graph binds to MockLlm. Modes are pinned per request with a `mode:` directive, so
no initial session state is needed.
"""
import os

os.environ["LLM_BACKEND"] = "mock"  # must precede the agent import

from apps.pdf_insight import config  # noqa: E402
from apps.pdf_insight.agent import root_agent  # noqa: E402


async def test_auto_mode_routes_through_extract_tables(converse):
    answers, state = await converse(root_agent, ["What is in this statement?"])
    assert state["active_pdf_mode"] == config.AUTO
    # mock router -> extract_tables -> summarize returns the rendered table text
    assert "Table 0" in answers[-1]


async def test_pinned_all_tables_publishes_table_text(converse):
    answers, state = await converse(
        root_agent,
        [f"mode: {config.ALL_TABLES_AS_TEXT} summarize the holdings"],
    )
    assert state["active_pdf_mode"] == config.ALL_TABLES_AS_TEXT
    # the deterministic stage published every table as text for the answerer
    assert "Table 0" in state["pdf_tables_text"]


async def test_pinned_sql_mode_ingests_and_delegates(converse):
    answers, state = await converse(
        root_agent,
        [f"mode: {config.SQL_FROM_TEXT} how many holdings are there?"],
    )
    assert state["active_pdf_mode"] == config.SQL_FROM_TEXT
    assert "db_path" in state          # ingestion ran
    assert answers[-1]                 # text2sql produced some answer


async def test_pinned_some_tables_publishes_table_text(converse):
    answers, state = await converse(
        root_agent,
        [f"mode: {config.SOME_TABLES_AS_TEXT} table 0 holdings"],
    )
    assert state["active_pdf_mode"] == config.SOME_TABLES_AS_TEXT
    assert "Table 0" in state["pdf_tables_text"]


async def test_pinned_stash_mode_routes(converse):
    # Routing-deep only: under the plain MockLlm the stash agent just replies; the
    # point is the coordinator dispatches to QUERY_STASH without crashing.
    # (Answer correctness for this mode lives in test_golden_answers.py / MockPdfLlm.)
    answers, state = await converse(
        root_agent,
        [f"mode: {config.QUERY_STASH} total vega by region"],
    )
    assert state["active_pdf_mode"] == config.QUERY_STASH
    assert answers[-1]  # produced a reply


async def test_sql_mode_reingests_when_pdf_changes(run_agent, monkeypatch):
    """Regression: a mid-session PDF switch must re-ingest, not reuse the stale db.

    State claims an earlier PDF was already ingested (db_path + db_source_pdf set);
    the active PDF is now the fixture. The old gate (`if not db_path`) skipped
    ingestion and answered from the previous document.
    """
    from apps.pdf_insight.modes import sql as sqlmod

    calls = []
    real = sqlmod.ingest_tables_to_sqlite
    monkeypatch.setattr(
        sqlmod, "ingest_tables_to_sqlite",
        lambda pdf_path, db_path: (calls.append(pdf_path), real(pdf_path, db_path))[1],
    )

    fixture = "tests/fixtures/risk_report.pdf"
    agent = sqlmod.build()[config.SQL_FROM_TEXT]
    await run_agent(agent, "how many rows?", {
        "pdf_path": fixture,
        "db_path": "/tmp/stale_old.pdf.sqlite",
        "db_source_pdf": "old.pdf",
    })
    assert calls == [fixture]  # re-ingested the new PDF, didn't reuse the stale db


async def test_native_bytes_mode_refused_under_mock(converse):
    answers, state = await converse(
        root_agent,
        [f"mode: {config.PDF_BYTES} read the whole document"],
    )
    assert state["active_pdf_mode"] == config.PDF_BYTES
    assert "gemini" in answers[-1].lower()
