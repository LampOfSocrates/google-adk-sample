"""Upload -> ingest into BOTH backends (runtime), and the branches it introduces.

The coordinator now treats a new active PDF as an upload: it ingests into the
per-document SQLite (replace) AND appends to the corpus (DuckDB), via the shared
`ingest_pdf_everywhere`. These tests cover the real behavior (corpus append +
idempotency, both-backend ingest, the coordinator hook) plus the defensive
branches the wiring adds. The pdf-conftest autouse fixture points both stores at a
temp dir, so nothing here touches the repo's data/.
"""
from apps.pdf_insight import config
from apps.pdf_insight.ingest import ingest_pdf_everywhere
from apps.pdf_insight.stores import DuckDBStore, SQLiteStore
from apps.pdf_insight.stores import duckdb_store

FIXTURE = "tests/fixtures/risk_report.pdf"


# ===========================================================================
# DuckDBStore.ingest_pdf — runtime corpus append (the new write path)
# ===========================================================================
def test_corpus_ingest_appends_idempotently_and_grows(tmp_path):
    """Re-ingesting a report is idempotent (rows for its date are replaced, not
    doubled); a different week grows the corpus. The documents table keys on
    filename, so 'two weeks' = two dated files."""
    wk1 = tmp_path / "risk_report_2026-05-01.pdf"
    wk2 = tmp_path / "risk_report_2026-05-08.pdf"
    wk1.write_bytes(open(FIXTURE, "rb").read())
    wk2.write_bytes(open(FIXTURE, "rb").read())
    store = DuckDBStore(str(tmp_path / "corpus.duckdb"))

    r1 = store.ingest_pdf(str(wk1))
    assert r1["status"] == "success" and r1["tables"] >= 1
    assert store.list_schema()["documents"]["count"] == 1

    n = store.run_select("SELECT COUNT(*) FROM t00 WHERE report_date='2026-05-01'")["rows"][0][0]
    store.ingest_pdf(str(wk1))  # same file/date -> replace
    assert store.run_select(
        "SELECT COUNT(*) FROM t00 WHERE report_date='2026-05-01'")["rows"][0][0] == n

    store.ingest_pdf(str(wk2))  # new week -> grows
    assert store.list_schema()["documents"]["count"] == 2
    assert store.run_select(
        "SELECT COUNT(DISTINCT report_date) FROM t00")["rows"][0][0] == 2


def test_corpus_ingest_derives_report_date_from_filename(tmp_path):
    """No explicit date -> parsed from the filename (risk_report_<date>.pdf)."""
    dated = tmp_path / "risk_report_2026-05-15.pdf"
    dated.write_bytes(open(FIXTURE, "rb").read())
    store = DuckDBStore(str(tmp_path / "corpus.duckdb"))
    out = store.ingest_pdf(str(dated))
    assert out["date"] == "2026-05-15"


def test_corpus_ingest_helper_edges(tmp_path, monkeypatch):
    """Exercise the ingestion helpers on awkward tables: a blank header (-> col_N
    fallback ident), two headers that collide after sanitizing (-> dedup), a
    non-numeric column (-> VARCHAR not DOUBLE), and a 'Total' row (-> is_total)."""
    crafted = [{
        "index": 0, "page": 1, "ncols": 3,
        "header": ["", "Vega!", "Vega?"],          # "" -> fallback; both Vega -> collide
        "rows": [["Total", "1,000", "x"], ["a", "2,500", "y"]],  # non-numeric col + Total row
    }]
    monkeypatch.setattr(duckdb_store.pdf, "extract_tables", lambda *a, **k: crafted)
    store = DuckDBStore(str(tmp_path / "corpus.duckdb"))
    out = store.ingest_pdf("ignored.pdf", report_date="2026-05-01")
    assert out["status"] == "success"
    # the 'Total' row is flagged so callers can exclude it
    totals = store.run_select("SELECT COUNT(*) FROM t00 WHERE is_total")["rows"][0][0]
    assert totals == 1


def test_corpus_ingest_bare_filename_db(tmp_path, monkeypatch):
    """ingest_pdf's `if parent:` guard — a bare-filename db has no dir to create."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        duckdb_store.pdf, "extract_tables",
        lambda *a, **k: [{"index": 0, "page": 1, "ncols": 1, "header": ["a"], "rows": [["1"]]}],
    )
    out = DuckDBStore("bare.duckdb").ingest_pdf("ignored.pdf", report_date="2026-05-01")
    assert out["status"] == "success"


def test_to_num_all_paths():
    """duckdb_store._to_num — every return: None, empty-after-strip, non-numeric,
    and the two parse successes (comma + percent stripping)."""
    assert duckdb_store._to_num(None) is None       # None short-circuit
    assert duckdb_store._to_num("   ") is None       # empty after strip
    assert duckdb_store._to_num("x") is None         # non-numeric -> ValueError
    assert duckdb_store._to_num("1,234") == 1234.0
    assert duckdb_store._to_num("-9%") == -9.0


# ===========================================================================
# ingest_pdf_everywhere — the shared upload handler (both backends)
# ===========================================================================
def test_ingest_everywhere_hits_both_backends():
    """A new upload lands in SQLite (queryable) and the corpus (1 document)."""
    state: dict = {}
    summary = ingest_pdf_everywhere(FIXTURE, state)
    assert summary["sqlite"]["status"] == "success"
    assert summary["corpus"]["status"] == "success"
    assert state["db_source_pdf"] == FIXTURE and state["ingested_pdf"] == FIXTURE
    assert SQLiteStore(state["db_path"]).list_schema()["status"] == "success"


def test_ingest_everywhere_skips_corpus_when_backend_unimplemented(monkeypatch):
    """CORPUS_BACKEND=postgres -> corpus ingest raises NotImplementedError, which
    the handler catches and reports as skipped (SQLite still ingests)."""
    monkeypatch.setenv("CORPUS_BACKEND", "postgres")
    summary = ingest_pdf_everywhere(FIXTURE, {})
    assert summary["sqlite"]["status"] == "success"
    assert summary["corpus"]["status"] == "skipped"


# ===========================================================================
# Coordinator hook — ingest on a new PDF, skip on the same, report on failure
# ===========================================================================
async def test_coordinator_ingests_on_new_pdf(converse, pdf_root_agent):
    """Driving root_agent with a fresh session ingests the active PDF once."""
    _, state = await converse(pdf_root_agent, ["what is in this report?"])
    assert state["ingested_pdf"].endswith("risk_report.pdf")
    assert "db_path" in state  # SQLite ingest ran via the coordinator


async def test_coordinator_skips_reingest_on_same_pdf(converse, pdf_root_agent):
    """Second turn on the same PDF takes the skip arc (ingested_pdf == path)."""
    answers, state = await converse(
        pdf_root_agent, ["first question?", "second question?"])
    assert state["ingested_pdf"].endswith("risk_report.pdf")
    assert len(answers) == 2  # both turns answered; no crash on the skip path


async def test_coordinator_reports_ingest_failure_without_crashing(converse, pdf_root_agent):
    """A bad PDF path makes ingestion fail; the coordinator reports it and the
    turn still completes (here pinned to pdfbytes, which refuses under mock)."""
    answers, state = await converse(
        pdf_root_agent, [f"mode: {config.PDF_BYTES} look at nope_missing.pdf"])
    assert state["active_pdf_mode"] == config.PDF_BYTES
    assert answers[-1]  # the mode still produced its (refusal) answer


def test_agent_module_reload_rebuilds_cleanly():
    """Streamlit reloads apps.pdf_insight.agent to rebind the model on a backend
    switch, which re-builds root_agent. build_router() must yield a fresh router
    each time, else the reload raises 'agent already has a parent'."""
    import importlib

    from apps.pdf_insight import agent as agent_mod

    importlib.reload(agent_mod)  # must not raise a pydantic ValidationError
    assert agent_mod.root_agent.name == "pdf_insight"
