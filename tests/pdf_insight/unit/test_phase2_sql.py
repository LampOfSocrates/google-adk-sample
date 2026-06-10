"""Phase 2 — SQLite ingestion + the read-only run_sql guard. No LLM."""
import pytest

from apps.pdf_insight.stores.sqlite_store import (
    ingest_tables_to_sqlite,
    list_sql_schema,
    run_sql,
)

PDF = "tests/fixtures/risk_report.pdf"


class _Ctx:
    """Minimal ToolContext stand-in — the SQL tools only read `.state`."""

    def __init__(self, state=None):
        self.state = state or {}


@pytest.fixture
def db(tmp_path):
    """Ingest the sample PDF into a throwaway SQLite file; yield its path."""
    path = str(tmp_path / "stmt.sqlite")
    result = ingest_tables_to_sqlite(PDF, path)
    assert result["status"] == "success"
    assert result["tables"]  # at least one table created
    return path


def test_ingest_creates_named_tables(db):
    schema = list_sql_schema(_Ctx({"db_path": db}))
    assert schema["status"] == "success"
    names = {t["table"] for t in schema["schema"]}
    assert "t0" in names  # one SQLite table per detected PDF table


def test_select_is_allowed(db):
    out = run_sql("SELECT * FROM t0", _Ctx({"db_path": db}))
    assert out["status"] == "success"
    assert "columns" in out and "rows" in out


def test_lowercase_select_against_sqlite_master(db):
    out = run_sql("select name from sqlite_master", _Ctx({"db_path": db}))
    assert out["status"] == "success"


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM t0",
        "INSERT INTO t0 VALUES (1)",
        "DROP TABLE t0",
        "UPDATE t0 SET x=1",
        "SELECT 1; DROP TABLE t0",          # multi-statement
        "WITH x AS (SELECT 1) DELETE FROM t0",  # not a leading SELECT
    ],
)
def test_writes_and_multistatements_are_rejected(db, query):
    out = run_sql(query, _Ctx({"db_path": db}))
    assert out["status"] == "error"


def test_missing_db_path_is_error():
    assert run_sql("SELECT 1", _Ctx())["status"] == "error"
    assert list_sql_schema(_Ctx())["status"] == "error"
