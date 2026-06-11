"""Multi-document, multi-KIND corpus — the regression test for the collision bug.

The old corpus merged every PDF's table by POSITION: each document's table 0 went
into a shared `t00`, so two DIFFERENT kinds of report (different columns) corrupted
each other or failed to ingest. Now each document gets its own physical tables and
documents are unioned into a `families` view only when they share a table's shape.

Scenario (multi-pdf AND multi-user): two users upload three documents into ONE
corpus — Alice a Greeks risk report, Bob a DIFFERENT-kind funding report, and Alice
a second (later-dated) Greeks report. Distinct filenames => distinct documents
(doc_id = filename), which is also how two users stay separate today.

We assert: the two kinds never share a table; same-kind reports DO union across
documents (so cross-report queries still work); and neither kind's columns leak
into the other's view.
"""
import os
import shutil

import pytest

from apps.pdf_insight.stores import DuckDBStore

GREEKS = "tests/pdf_insight/fixtures/risk_report.pdf"     # kind A (16 Greek tables)
FUNDING = "tests/pdf_insight/fixtures/funding_report.pdf"  # kind B (4 funding tables)


@pytest.fixture
def corpus(tmp_path):
    """Three uploads (2 users, 2 kinds, 2 dates) ingested into one corpus DB."""
    uploads = [
        ("alice_risk_2026-05-01.pdf", GREEKS),
        ("bob_funding_2026-05-01.pdf", FUNDING),
        ("alice_risk_2026-05-08.pdf", GREEKS),
    ]
    store = DuckDBStore(str(tmp_path / "corpus.duckdb"))
    for name, src in uploads:
        dest = tmp_path / name
        shutil.copy(src, dest)
        assert store.ingest_pdf(str(dest))["status"] == "success"
    return store


def _greeks_region(store):
    """The Greeks 'Risk Summary by Region' family: region + vega, no asset_class.
    (Runtime ingest carries no golden titles, so we identify families by columns.)"""
    for f in store.list_schema()["tables"]:
        cols = f["columns"]
        if "region" in cols and "vega_k" in cols and "asset_class" not in cols:
            return f
    raise AssertionError("Greeks region-summary family not found")


def _funding_currency(store):
    """The funding 'Funding by Currency' family — identified by its hqla_k column."""
    for f in store.list_schema()["tables"]:
        if "hqla_k" in f["columns"]:
            return f
    raise AssertionError("funding currency family not found")


def test_three_documents_ingested(corpus):
    assert corpus.list_schema()["documents"]["count"] == 3


def test_kinds_do_not_collide(corpus):
    """The core fix: a Greeks table and a funding table that sit at the same index
    are DIFFERENT families (different views), and neither's columns leak into the
    other. The old positional design merged them into one corrupt `t00`."""
    greeks = _greeks_region(corpus)
    funding = _funding_currency(corpus)
    assert greeks["table"] != funding["table"]
    # the Greeks view has no funding columns...
    assert not any(c in greeks["columns"] for c in ("funding_m", "hqla_k", "inflow_m"))
    # ...and the funding view has no Greeks measure columns.
    assert not any(c in funding["columns"] for c in ("vega_k", "delta_k", "gamma_k"))


def test_same_kind_reports_union_across_documents(corpus):
    """Two Greeks reports share their families (docs=2); the lone funding report's
    families stay at docs=1 — proving same-kind reports still accumulate."""
    assert _greeks_region(corpus)["documents"] == 2
    assert _funding_currency(corpus)["documents"] == 1
    # every Greeks family unions both reports; every funding family has just one.
    fams = corpus.list_schema()["tables"]
    greeks = [f for f in fams if "delta_k" in f["columns"] or "vega_k" in f["columns"]]
    assert greeks and all(f["documents"] == 2 for f in greeks)


def test_cross_report_query_on_shared_greeks_view(corpus):
    """A 'across all reports' query on the Greeks region view spans both weeks —
    the time-series use case the union views preserve."""
    view = _greeks_region(corpus)["table"]
    out = corpus.run_select(f'SELECT COUNT(DISTINCT report_date) FROM "{view}"')
    assert out["status"] == "success"
    assert out["rows"][0][0] == 2  # both Greeks reports present in the one view


def test_funding_view_is_isolated_and_queryable(corpus):
    """The funding view answers on its own columns and never exposes Greeks data."""
    view = _funding_currency(corpus)["table"]
    out = corpus.run_select(f'SELECT * FROM "{view}"')
    assert out["status"] == "success"
    assert "hqla_k" in out["columns"] and "vega_k" not in out["columns"]
    # only the one funding report's rows live here (one report_date).
    dates = corpus.run_select(f'SELECT COUNT(DISTINCT report_date) FROM "{view}"')
    assert dates["rows"][0][0] == 1
