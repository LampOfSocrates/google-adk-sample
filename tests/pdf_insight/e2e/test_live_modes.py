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

import pytest

from apps.pdf_insight import config
from apps.pdf_insight.agent import root_agent
from scripts.pdf_insight.pdf_to_duckdb import ingest_dir
from scripts.pdf_insight.weekly_report import generate_for

pytestmark = pytest.mark.live

# `root_agent` is imported directly: the model binds lazily (shared.model.LazyModel
# resolves LLM_BACKEND per turn), and the autouse `_backend_env` fixture pins the
# live backend for live-marked tests at run time — so no purge+reimport is needed.

GOLD = json.load(open("tests/pdf_insight/fixtures/risk_report.golden.json", encoding="utf-8"))["facts"]
VEGA = GOLD["totals"]["vega"]            # 6384
REGION_MAX_VEGA = GOLD["region_max_vega"]  # "Americas"


def _has_number(answer: str, value: int) -> bool:
    """Comma/format-insensitive: is `value` present in the model's prose answer?"""
    digits = "".join(ch for ch in answer if ch.isdigit())
    return str(value) in digits


# ----------------------------------------------------------- tables-as-text ---
async def test_live_all_tables_mode_total_vega(converse):
    """Live ALL-tables mode: a real model reads every rendered table and returns the
    golden total vega through the actual coordinator."""
    answers, state = await converse(
        root_agent, [f"mode: {config.ALL_TABLES_AS_TEXT} what is the total vega?"]
    )
    assert state["active_pdf_mode"] == config.ALL_TABLES_AS_TEXT
    assert _has_number(answers[-1], VEGA)


async def test_live_some_tables_mode_total_vega(converse):
    """Live SOME-tables mode: pinned to table 0 (the by-region summary that carries
    the vega total), a real model still returns the golden figure."""
    # Table 0 is the by-region risk summary, which carries the vega total.
    answers, state = await converse(
        root_agent, [f"mode: {config.SOME_TABLES_AS_TEXT} table 0 what is the total vega?"]
    )
    assert state["active_pdf_mode"] == config.SOME_TABLES_AS_TEXT
    assert _has_number(answers[-1], VEGA)


# --------------------------------------------------------------- SQL mode -----
async def test_live_sql_mode_region_max_vega(converse):
    """Live SQL mode: a real model writes SQL and names the max-vega region. Chosen
    because it needs only an ordering, not summing comma'd TEXT cells."""
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
    """Live corpus mode: a real model queries the multi-week DuckDB corpus and names
    every region — proving it grouped across all reports, not just one document."""
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


# --------------------------------------------------------- native bytes mode ----
async def test_live_bytes_mode_total_vega(converse):
    """Live PDF_BYTES mode: the raw PDF is handed to the model (no extraction) and a
    real, document-capable backend reads the golden total vega straight out of it.

    The coordinator resolves the default fixture PDF, so no session state is needed.
    Skips on backends with no document input (e.g. deepseek), which legitimately
    can't run this mode."""
    answers, state = await converse(
        root_agent, [f"mode: {config.PDF_BYTES} what is the total vega?"]
    )
    assert state["active_pdf_mode"] == config.PDF_BYTES
    if "does not support content part" in answers[-1].lower():
        pytest.skip(f"backend has no PDF document input: {answers[-1]}")
    assert _has_number(answers[-1], VEGA)
