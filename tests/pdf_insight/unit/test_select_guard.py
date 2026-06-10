"""Unit — the read-only trust boundary (stores/base.py) + scalar coercion.

`validate_select` is THE security boundary every backend shares: the LLM writes
SQL, this regex pair decides whether it runs. It is pure (no engine, no db), so it
belongs here at the unit layer where every branch can be enumerated cheaply and
table-driven rather than exercised through a runner.

We pin three things:
  * what the guard ACCEPTS (single read-only SELECT/WITH, in its many shapes);
  * what it REJECTS (multi-statement, non-SELECT, write/DDL keywords);
  * its KNOWN false-positives (string-literal- and comment-naive). These are
    documented as xfail so a future hardening of the guard turns them green
    (xpass) and flags that the spec moved — they are correctness gaps, not holes:
    the leading-SELECT + single-statement rules keep it read-only regardless.

Also covers `jsonable` (stores/base.py) and `_to_num` (duckdb_store.py), the two
pure coercion helpers the result path and corpus ingest depend on.
"""
import datetime as dt
from decimal import Decimal

import pytest

from apps.pdf_insight.stores.base import jsonable, validate_select
from apps.pdf_insight.stores.duckdb_store import _to_num


# --------------------------------------------------------- validate_select ----
@pytest.mark.parametrize("query", [
    "SELECT 1",
    "select 1",                                            # case-insensitive
    "  SELECT 1  ",                                        # surrounding whitespace
    "SELECT * FROM t0;",                                   # single trailing semicolon stripped
    "WITH x AS (SELECT 1) SELECT * FROM x",                # CTE
    "  \n  with x as (select 1) select * from x",          # leading newline + lower WITH
    "SELECT REPLACE(col, ',', '') FROM t",                 # REPLACE() scalar is read-only, allowed
    "SELECT report_date, SUM(vega) FROM t00 WHERE NOT is_total GROUP BY report_date",
])
def test_accepts_single_readonly_select(query):
    assert validate_select(query) is None


@pytest.mark.parametrize("query, fragment", [
    ("SELECT 1; SELECT 2",                "single statement"),   # multi-statement
    ("SELECT 1; DROP TABLE t",            "single statement"),   # injection via 2nd stmt
    ("",                                  "SELECT / WITH"),       # empty
    ("   ",                               "SELECT / WITH"),       # whitespace-only
    ("EXPLAIN SELECT 1",                  "SELECT / WITH"),       # not leading SELECT/WITH
    ("UPDATE t SET x=1",                  "SELECT / WITH"),       # leading non-SELECT
    ("INSERT INTO t VALUES (1)",          "SELECT / WITH"),
    ("DELETE FROM t",                     "SELECT / WITH"),
    ("WITH x AS (DELETE FROM t ...) SELECT 1", "Write/DDL"),      # write hidden in a CTE body
    ("SELECT * FROM t; INSERT INTO t VALUES(1)", "single statement"),
])
def test_rejects_non_readonly(query, fragment):
    err = validate_select(query)
    assert err is not None and fragment in err


@pytest.mark.parametrize("keyword", [
    "insert", "update", "delete", "drop", "alter", "create",
    "attach", "copy", "pragma", "install", "load", "export",
])
def test_rejects_each_forbidden_keyword_in_a_leading_select(keyword):
    # Keyword appears as a bare word after a leading SELECT — must be blocked.
    assert "Write/DDL" in validate_select(f"SELECT * FROM t WHERE x={keyword} 1")


# --- KNOWN limitations: regex guard is string-literal- & comment-naive. -------
# These document false-POSITIVES (legit read-only SQL wrongly blocked). They are
# correctness gaps, not security holes. xfail(strict) so hardening flips them to
# xpass and signals the contract changed. If you fix the guard, delete the xfail.
@pytest.mark.xfail(strict=True, reason="guard is string-literal-naive: keyword inside a string literal false-rejected")
def test_keyword_inside_string_literal_should_be_allowed():
    assert validate_select("SELECT * FROM t WHERE region = 'insert-corp'") is None


@pytest.mark.xfail(strict=True, reason="guard is comment-naive: ; inside a -- comment false-rejected as multi-statement")
def test_semicolon_in_line_comment_should_be_allowed():
    assert validate_select("SELECT 1 -- a; b\n") is None


@pytest.mark.xfail(strict=True, reason="guard is comment-naive: keyword inside a block comment false-rejected")
def test_keyword_in_block_comment_should_be_allowed():
    assert validate_select("SELECT 1 /* no drop here */ FROM t") is None


def test_keyword_list_is_not_exhaustive_documented():
    """DOCUMENTED gap, not a security claim: the forbidden-keyword list is a
    denylist, so a write verb it doesn't enumerate (e.g. `INTO outfile`, `merge`,
    `truncate`, `replace into`) slips PAST validate_select. Read-only safety does
    NOT rely on this list being complete — it relies on the engine connection
    being opened read-only (PRAGMA query_only / read_only=True) AND the
    single-statement + leading-SELECT rules. This test pins the denylist's
    incompleteness so nobody mistakes it for the boundary. `INTO outfile` is
    inert on SQLite/DuckDB; it only matters if a future backend honors it."""
    assert validate_select("SELECT 1 INTO outfile") is None  # passes the guard
    assert validate_select("SELECT * FROM t WHERE x = (TRUNCATE 1)") is None


# ----------------------------------------------------------------- jsonable ---
@pytest.mark.parametrize("value, expected", [
    (1, 1),
    (1.5, 1.5),
    ("x", "x"),
    (None, None),
    (dt.date(2026, 5, 1), "2026-05-01"),
    (dt.datetime(2026, 5, 1, 9, 30), "2026-05-01T09:30:00"),
    (Decimal("3.14"), "3.14"),                  # unrecognised -> str()
    (True, True),                               # bool is an int subclass: passes through
])
def test_jsonable_coercion(value, expected):
    assert jsonable(value) == expected


# ------------------------------------------------------------------ _to_num ---
@pytest.mark.parametrize("cell, expected", [
    ("9,334", 9334.0),       # thousands-comma stripped
    ("-955", -955.0),        # negative
    ("12.5%", 12.5),         # percent stripped
    ("  42 ", 42.0),         # whitespace stripped
    ("1,234.56", 1234.56),
    (None, None),            # null
    ("", None),              # empty
    ("   ", None),           # whitespace-only
    ("%", None),             # percent-only -> empty after strip
    ("n/a", None),           # non-numeric
    ("Americas", None),      # text label
])
def test_to_num_parsing(cell, expected):
    assert _to_num(cell) == expected
