"""Corpus trust boundary + cross-week correctness — the DuckDB layer. No LLM.

Builds a small, fixed temp corpus (a few dated reports ingested into a throwaway
DuckDB) and exercises stores/duckdb_store.py directly: the run_corpus_sql guards, the schema
registry, _jsonable coercion, and that the numbers match the golden files.
"""
import datetime as dt
import json

import pytest

from apps.pdf_insight.stores.corpus_tools import list_corpus_schema, run_corpus_sql
from apps.pdf_insight.stores.duckdb_store import _jsonable
from scripts.pdf_insight.pdf_to_duckdb import ingest_dir
from scripts.pdf_insight.weekly_report import generate_for

WEEKS = [dt.date(2026, 5, 1), dt.date(2026, 5, 8), dt.date(2026, 5, 15)]


class _Ctx:
    """Minimal ToolContext stand-in — the duckdb tools only read `.state`."""

    def __init__(self, state=None):
        self.state = state or {}


def _view(db: str, title: str) -> str:
    """Resolve a family view name from the registry. Corpus table names are now
    per-shape union VIEWS (fam_*), not positional t00 — look them up by title."""
    sch = list_corpus_schema(_Ctx({"corpus_db": db}))
    return next(t["table"] for t in sch["tables"] if t["title"] == title)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """Generate WEEKS dated reports, ingest into one DuckDB; return (db, goldens)."""
    d = tmp_path_factory.mktemp("corpus")
    goldens = {}
    for wk in WEEKS:
        generate_for(wk, str(d))
        gp = d / f"risk_report_{wk.isoformat()}.golden.json"
        goldens[wk.isoformat()] = json.load(open(gp, encoding="utf-8"))["facts"]
    db = str(d / "corpus.duckdb")
    summary = ingest_dir(db, str(d))
    assert len(summary) == len(WEEKS)
    return db, goldens


# ---------------------------------------------------------------- registry ----
def test_schema_lists_all_tables_and_coverage(corpus):
    db, _ = corpus
    out = list_corpus_schema(_Ctx({"corpus_db": db}))
    assert out["status"] == "success"
    assert out["documents"]["count"] == len(WEEKS)
    assert out["documents"]["from"] == WEEKS[0].isoformat()
    assert out["documents"]["to"] == WEEKS[-1].isoformat()
    assert len(out["tables"]) == 16  # 16 distinct shapes -> 16 family views
    region = next(t for t in out["tables"] if t["title"] == "Risk Summary by Region")
    assert any("vega" in c for c in region["columns"])
    assert region["documents"] == len(WEEKS)  # its view unions every week's table


# ------------------------------------------------------------ trust boundary --
def test_select_and_with_are_allowed(corpus):
    db, _ = corpus
    ctx = _Ctx({"corpus_db": db})
    assert run_corpus_sql("SELECT 1", ctx)["status"] == "success"
    assert run_corpus_sql("WITH x AS (SELECT 1 AS n) SELECT n FROM x", ctx)["status"] == "success"


@pytest.mark.parametrize("query", [
    "DELETE FROM t00",
    "DROP TABLE t00",
    "UPDATE t00 SET region='x'",
    "INSERT INTO t00 VALUES (1)",
    "ATTACH 'evil.db' AS e",
    "COPY t00 TO 'leak.csv'",
    "SELECT 1; SELECT 2",                       # multi-statement
    "WITH x AS (SELECT 1) DELETE FROM t00",     # not a leading SELECT/WITH-SELECT
])
def test_writes_and_multistatements_rejected(corpus, query):
    db, _ = corpus
    assert run_corpus_sql(query, _Ctx({"corpus_db": db}))["status"] == "error"


def test_missing_db_is_clean_error(tmp_path):
    ctx = _Ctx({"corpus_db": str(tmp_path / "nope.duckdb")})
    assert run_corpus_sql("SELECT 1", ctx)["status"] == "error"
    assert list_corpus_schema(ctx)["status"] == "error"


def test_date_column_serializes_as_iso_string(corpus):
    db, _ = corpus
    view = _view(db, "Risk Summary by Region")
    out = run_corpus_sql(f'SELECT DISTINCT report_date FROM "{view}" ORDER BY 1', _Ctx({"corpus_db": db}))
    assert out["status"] == "success"
    # DATE must survive ADK's JSON function_response as a string, not a date object.
    assert out["rows"][0][0] == WEEKS[0].isoformat()
    assert all(isinstance(r[0], str) for r in out["rows"])


def test_jsonable_coercions():
    assert _jsonable(dt.date(2026, 5, 1)) == "2026-05-01"
    assert _jsonable(None) is None
    assert _jsonable(3.5) == 3.5
    assert _jsonable("x") == "x"


# ----------------------------------------------------- cross-week correctness -
def test_per_week_total_vega_matches_golden(corpus):
    db, goldens = corpus
    ctx = _Ctx({"corpus_db": db})
    view = _view(db, "Risk Summary by Region")
    for wk, facts in goldens.items():
        out = run_corpus_sql(
            f"SELECT SUM(vega_k) FROM \"{view}\" WHERE NOT is_total AND report_date='{wk}'", ctx
        )
        assert int(out["rows"][0][0]) == facts["totals"]["vega"]


def test_trend_returns_one_row_per_week_in_order(corpus):
    db, _ = corpus
    view = _view(db, "Risk Summary by Region")
    out = run_corpus_sql(
        f'SELECT report_date, SUM(vega_k) FROM "{view}" WHERE NOT is_total '
        "GROUP BY report_date ORDER BY report_date", _Ctx({"corpus_db": db})
    )
    dates = [r[0] for r in out["rows"]]
    assert dates == [wk.isoformat() for wk in WEEKS]


def test_is_total_flag_excludes_subtotal(corpus):
    db, _ = corpus
    ctx = _Ctx({"corpus_db": db})
    view = _view(db, "Risk Summary by Region")
    without = run_corpus_sql(f'SELECT SUM(vega_k) FROM "{view}" WHERE NOT is_total', ctx)["rows"][0][0]
    with_tot = run_corpus_sql(f'SELECT SUM(vega_k) FROM "{view}"', ctx)["rows"][0][0]
    # the per-table Total row duplicates the sum of its rows -> 2x when included.
    assert with_tot == pytest.approx(without * 2)
