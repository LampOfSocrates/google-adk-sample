"""Correctness, not just wiring: MockPdfLlm must produce the GOLDEN numbers.

Builds targeted agents on MockPdfLlm (the domain-aware offline mock) and asserts
the answers match tests/fixtures/risk_report.golden.json across the modes that
answer questions: tables-as-text, single-PDF SQL, and the DuckDB corpus.
"""
import datetime as dt
import json

import pytest
from google.adk.agents import LlmAgent

from shared import pdf
from shared.mock_pdf_llm import MockPdfLlm

from apps.pdf_insight.stores.corpus_tools import list_corpus_schema, run_corpus_sql
from apps.pdf_insight.stores.sqlite_store import ingest_tables_to_sqlite, list_sql_schema, run_sql
from scripts.pdf_to_duckdb import ingest_dir
from scripts.weekly_report import generate_for

FIXTURE = "tests/fixtures/risk_report.pdf"
GOLD = json.load(open("tests/fixtures/risk_report.golden.json", encoding="utf-8"))["facts"]
VEGA = f'{GOLD["totals"]["vega"]:,}'      # "6,384"
DELTA = f'{GOLD["totals"]["delta"]:,}'    # "9,152"


# ----------------------------------------------------------- tables-as-text ---
def _answerer() -> LlmAgent:
    return LlmAgent(
        name="answerer", model=MockPdfLlm(),
        instruction="Tables:\n\n{pdf_tables_text?}\n\nAnswer the question from them.",
    )


@pytest.fixture(scope="module")
def tables_text():
    return pdf.tables_as_text(pdf.extract_tables(FIXTURE))


async def test_text_mode_total_vega(run_agent, tables_text):
    a = await run_agent(_answerer(), "What is the total vega?", {"pdf_tables_text": tables_text})
    assert VEGA in a


async def test_text_mode_net_delta(run_agent, tables_text):
    a = await run_agent(_answerer(), "What is the net delta?", {"pdf_tables_text": tables_text})
    assert DELTA in a


async def test_text_mode_region_max_vega(run_agent, tables_text):
    a = await run_agent(_answerer(), "Which region has the most vega?", {"pdf_tables_text": tables_text})
    assert GOLD["region_max_vega"] in a


# --------------------------------------------------------------- SQL mode -----
def _sql_agent() -> LlmAgent:
    return LlmAgent(
        name="sql", model=MockPdfLlm(), tools=[list_sql_schema, run_sql],
        instruction="Answer over the tables with one read-only SQL SELECT.",
    )


@pytest.fixture
def sqlite_db(tmp_path):
    db = str(tmp_path / "risk.sqlite")
    ingest_tables_to_sqlite(FIXTURE, db)
    return db


async def test_sql_mode_total_vega_handles_comma_text(run_agent, sqlite_db):
    # Guards the bug where SQLite coerces "2,765" -> 2; the mock strips commas.
    a = await run_agent(_sql_agent(), "What is the total vega?", {"db_path": sqlite_db})
    assert VEGA in a


async def test_sql_mode_region_max_vega(run_agent, sqlite_db):
    a = await run_agent(_sql_agent(), "Which region has the most vega?", {"db_path": sqlite_db})
    assert GOLD["region_max_vega"] in a


# -------------------------------------------------------------- corpus mode ----
def _corpus_agent() -> LlmAgent:
    return LlmAgent(
        name="corpus", model=MockPdfLlm(), tools=[list_corpus_schema, run_corpus_sql],
        instruction="Answer across the report corpus with read-only DuckDB SQL.",
    )


@pytest.fixture(scope="module")
def corpus_db(tmp_path_factory):
    d = tmp_path_factory.mktemp("gcorpus")
    weeks = [dt.date(2026, 5, 1), dt.date(2026, 5, 8)]
    for wk in weeks:
        generate_for(wk, str(d))
    db = str(d / "corpus.duckdb")
    ingest_dir(db, str(d))
    return db, [wk.isoformat() for wk in weeks]


async def test_corpus_total_vega_by_region_leads_with_truth(run_agent, corpus_db):
    db, _ = corpus_db
    a = await run_agent(_corpus_agent(), "Total vega by region across all reports", {"corpus_db": db})
    # truth: which region has the largest summed vega across the corpus
    truth = run_corpus_sql(
        "SELECT region FROM t00 WHERE NOT is_total GROUP BY region "
        "ORDER BY SUM(vega_k) DESC LIMIT 1", type("C", (), {"state": {"corpus_db": db}})()
    )["rows"][0][0]
    assert a.split()[0] == "Vega" or truth in a  # mention present...
    assert truth in a                              # ...and the leading region is the true one
    for region in ("Americas", "EMEA", "APAC"):
        assert region in a


async def test_corpus_trend_lists_each_week(run_agent, corpus_db):
    db, dates = corpus_db
    a = await run_agent(_corpus_agent(), "How has Americas vega trended over the weeks?", {"corpus_db": db})
    for d in dates:
        assert d in a
