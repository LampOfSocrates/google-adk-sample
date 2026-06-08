"""Live smoke tests — each answering mode against a REAL model (not MockPdfLlm).

The offline suite proves correctness against the golden numbers via MockPdfLlm, a
domain-aware *heuristic*. These tests close the remaining gap: that a real model,
driven through the actual coordinator, still produces the golden answer end-to-end.

One assertion per mode, kept tolerant (comma-insensitive number match) so a model's
formatting choices don't flake the test. Opt-in:

    pytest -m live tests/pdf                      # gemini (default)
    pytest -m live tests/pdf --backend deepseek   # another backend (needs its key)

The pinned modes (ALL/SOME/SQL) resolve the default fixture PDF themselves, so no
session state is needed; the corpus mode reads a temp DuckDB via PDF_CORPUS_DB.
"""
import datetime as dt
import json
import os

# Re-assert the live backend right before the agent binds its model, so the
# offline modules' LLM_BACKEND=mock (set during collection) can't reach us.
os.environ["LLM_BACKEND"] = os.environ.get("LIVE_BACKEND", "gemini")

import pytest  # noqa: E402

from apps.pdf_insight import config  # noqa: E402
from apps.pdf_insight.agent import root_agent  # noqa: E402
from scripts.pdf_to_duckdb import ingest_dir  # noqa: E402
from scripts.weekly_report import generate_for  # noqa: E402

pytestmark = pytest.mark.live

GOLD = json.load(open("tests/fixtures/risk_report.golden.json", encoding="utf-8"))["facts"]
VEGA = GOLD["totals"]["vega"]            # 6384
REGION_MAX_VEGA = GOLD["region_max_vega"]  # "Americas"


def _has_number(answer: str, value: int) -> bool:
    """Comma/format-insensitive: is `value` present in the model's prose answer?"""
    digits = "".join(ch for ch in answer if ch.isdigit())
    return str(value) in digits


# ----------------------------------------------------------- tables-as-text ---
async def test_live_all_tables_mode_total_vega(converse):
    answers, state = await converse(
        root_agent, [f"mode: {config.ALL_TABLES_AS_TEXT} what is the total vega?"]
    )
    assert state["active_pdf_mode"] == config.ALL_TABLES_AS_TEXT
    assert _has_number(answers[-1], VEGA)


async def test_live_some_tables_mode_total_vega(converse):
    # Table 0 is the by-region risk summary, which carries the vega total.
    answers, state = await converse(
        root_agent, [f"mode: {config.SOME_TABLES_AS_TEXT} table 0 what is the total vega?"]
    )
    assert state["active_pdf_mode"] == config.SOME_TABLES_AS_TEXT
    assert _has_number(answers[-1], VEGA)


# --------------------------------------------------------------- SQL mode -----
async def test_live_sql_mode_region_max_vega(converse):
    # Correctness check that doesn't hinge on the model summing comma'd TEXT cells:
    # "which region" only needs an ordering, and the by-region table is small.
    answers, state = await converse(
        root_agent, [f"mode: {config.SQL_FROM_TEXT} which region has the most vega?"]
    )
    assert state["active_pdf_mode"] == config.SQL_FROM_TEXT
    assert REGION_MAX_VEGA.lower() in answers[-1].lower()


# -------------------------------------------------------------- corpus mode ----
@pytest.fixture(scope="module")
def corpus_db(tmp_path_factory):
    """Build a small 2-week DuckDB corpus; return its path."""
    d = tmp_path_factory.mktemp("live_corpus")
    for wk in (dt.date(2026, 5, 1), dt.date(2026, 5, 8)):
        generate_for(wk, str(d))
    db = str(d / "corpus.duckdb")
    ingest_dir(db, str(d))
    return db


async def test_live_corpus_mode_vega_by_region(converse, corpus_db, monkeypatch):
    # The corpus tools read PDF_CORPUS_DB when state has no corpus_db (converse can't
    # seed state). DuckDB stores numerics as DOUBLE, so summing here is clean.
    monkeypatch.setenv("PDF_CORPUS_DB", corpus_db)
    answers, state = await converse(
        root_agent,
        [f"mode: {config.QUERY_CORPUS} total vega by region across all reports"],
    )
    assert state["active_pdf_mode"] == config.QUERY_CORPUS
    # It actually grouped by region and read the corpus: every region is named.
    for region in ("Americas", "EMEA", "APAC"):
        assert region in answers[-1]
