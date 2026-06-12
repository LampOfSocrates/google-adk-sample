"""DuckDB backend — the whole-corpus store for LLM_QUERIES_CORPUS.

Holds MANY, possibly DIFFERENT-KIND PDFs without collision. The old design merged
by POSITION — every PDF's table 0 went into a shared `t00` — which silently
corrupted the corpus the moment two documents had different shapes. Now:

  * each uploaded document gets its OWN physical tables  (d<slug>_t<idx>)
  * a `families` view unions every document's table that shares the same
    (position, column-shape), so "across all reports" queries still work
  * registries map it all:
      documents   — one row per uploaded PDF (doc_id = filename, report_date, ...)
      doc_tables  — each doc's tables: (doc_id, table_index) -> physical table + family
      families    — one row per distinct (index, columns+types): the union VIEW,
                    its title and columns

So same-kind reports still accumulate (now through their family view, e.g. the
weekly risk report's 16 families == the old t00..t15), while a different KIND of
PDF forms its own families and never lands in another doc's table.

Queries open the DB `read_only`; `ingest_pdf` opens it read-write to add/replace
ONE document (idempotent per doc_id). The same path runs at upload time and from
the offline CLI (scripts/pdf_insight/pdf_to_duckdb.py).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re

import duckdb

from backend.shared import pdf_extractor as pdf

from .base import SqlStore, jsonable

# Back-compat alias: tests refer to this module's coercion helper as `_jsonable`.
_jsonable = jsonable

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# --- pure ingestion helpers (shared by runtime + the offline CLI) --------------
def _report_date(filename: str) -> str | None:
    m = _DATE_RE.search(filename)
    return m.group(1) if m else None


def _sql_ident(name: str, fallback: str) -> str:
    """Header cell -> safe, lower-snake DuckDB identifier. 'Vega($k)' -> 'vega_k'.

    Deliberately stricter than sqlite_store._sql_ident (which preserves case and
    replaces `\\W` 1:1): the corpus is queried by hand across weeks, so columns
    must be lower_snake and identical report to report."""
    ident = re.sub(r"\W+", "_", name).strip("_").lower()
    if not ident or ident[0].isdigit():
        ident = f"c_{ident}" if ident else fallback
    return ident


def _to_num(cell):
    """Parse '9,334' / '-955' -> float, else None ('%'/blanks -> None)."""
    if cell is None:
        return None
    s = cell.replace(",", "").replace("%", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _columns_for(table: dict) -> list[str]:
    cols, seen = [], {}
    for i, raw in enumerate(table["header"]):
        col = _sql_ident(raw, f"col_{i}")
        while col in seen:
            seen[col] += 1
            col = f"{col}_{seen[col]}"
        seen.setdefault(col, 0)
        cols.append(col)
    return cols


def _numeric_mask(table: dict, ncols: int) -> list[bool]:
    """A column is numeric iff every non-empty data cell parses as a number.
    Column 0 (the row label) is always text."""
    numeric = [True] * ncols
    numeric[0] = False
    for r in table["rows"]:
        for c in range(1, ncols):
            cell = r[c] if c < len(r) else ""
            if cell and _to_num(cell) is None:
                numeric[c] = False
    return numeric


def _doc_slug(doc_id: str) -> str:
    """Identifier-safe, bounded handle for a document's physical tables."""
    return "d" + hashlib.sha1(doc_id.encode()).hexdigest()[:10]


def _family_id(table_index: int, cols: list[str], types: list[str]) -> str:
    """Stable id for a (position, column-shape) family.

    Two documents' tables share a family — and thus a UNION view — iff they sit at
    the same table index AND have identical column names+types (so the union is
    valid and the rows mean the same thing). Different KINDS of table never merge."""
    shape = json.dumps([table_index, list(zip(cols, types))], sort_keys=True)
    return f"{table_index:02d}_{hashlib.sha1(shape.encode()).hexdigest()[:8]}"


def _ensure_registries(con) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            doc_id VARCHAR PRIMARY KEY, filename VARCHAR, report_date DATE,
            n_pages INTEGER, n_tables INTEGER, ingested_at TIMESTAMP
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS doc_tables (
            doc_id VARCHAR, table_index INTEGER, phys_table VARCHAR, family_id VARCHAR,
            title VARCHAR, columns VARCHAR, n_rows INTEGER,
            PRIMARY KEY (doc_id, table_index)
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS families (
            family_id VARCHAR PRIMARY KEY, view_name VARCHAR, title VARCHAR, columns VARCHAR
        )""")


def _drop_all_views(con) -> None:
    """Drop every family view so the physical tables underneath can be replaced
    (DuckDB forbids dropping a table a view still depends on). Rebuilt at the end."""
    for (view,) in con.execute("SELECT view_name FROM families").fetchall():
        con.execute(f'DROP VIEW IF EXISTS "{view}"')


def _ingest_table(con, table: dict, report_date: str, source_file: str,
                  doc_id: str, slug: str, title_for) -> int:
    """Create this document's own physical table for one extracted table, insert
    its rows, and register it under its (index, shape) family."""
    idx = table["index"]
    cols = _columns_for(table)
    ncols = len(cols)
    numeric = _numeric_mask(table, ncols)
    types = ["DOUBLE" if numeric[i] else "VARCHAR" for i in range(ncols)]
    fam = _family_id(idx, cols, types)
    phys = f"{slug}_t{idx:02d}"
    coldefs = ", ".join(f'"{c}" {types[i]}' for i, c in enumerate(cols))
    con.execute(f'''
        CREATE TABLE "{phys}" (
            report_date DATE, source_file VARCHAR, doc_id VARCHAR, row_index INTEGER,
            is_total BOOLEAN, {coldefs}
        )''')
    placeholders = ", ".join("?" for _ in range(5 + ncols))
    for ri, raw in enumerate(table["rows"]):
        is_total = bool(raw and str(raw[0]).strip().lower() == "total")
        vals = [
            _to_num(raw[c] if c < len(raw) else "") if numeric[c]
            else (raw[c] if c < len(raw) else "")
            for c in range(ncols)
        ]
        con.execute(f'INSERT INTO "{phys}" VALUES ({placeholders})',
                    [report_date, source_file, doc_id, ri, is_total, *vals])
    title = title_for(idx) if title_for else f"Table {idx}"
    con.execute("INSERT INTO doc_tables VALUES (?, ?, ?, ?, ?, ?, ?)",
                [doc_id, idx, phys, fam, title, json.dumps(cols), len(table["rows"])])
    con.execute("""
        INSERT INTO families (family_id, view_name, title, columns)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (family_id) DO UPDATE SET title = excluded.title,
                                              columns = excluded.columns
    """, [fam, f"fam_{fam}", title, json.dumps(cols)])
    return len(table["rows"])


def _rebuild_views(con) -> None:
    """(Re)create one UNION ALL view per family over its member physical tables.
    Families that lost all their members (a document was replaced) are removed."""
    for fam_id, view, cols_json in con.execute(
        "SELECT family_id, view_name, columns FROM families"
    ).fetchall():
        members = con.execute(
            'SELECT phys_table FROM doc_tables WHERE family_id = ? ORDER BY phys_table',
            [fam_id],
        ).fetchall()
        con.execute(f'DROP VIEW IF EXISTS "{view}"')
        if not members:
            con.execute("DELETE FROM families WHERE family_id = ?", [fam_id])
            continue
        cols = json.loads(cols_json)
        select = ", ".join(["report_date", "source_file", "doc_id", "row_index",
                            "is_total", *[f'"{c}"' for c in cols]])
        union = " UNION ALL ".join(f'SELECT {select} FROM "{m[0]}"' for m in members)
        con.execute(f'CREATE VIEW "{view}" AS {union}')


class DuckDBStore(SqlStore):
    """The single whole-corpus DuckDB database (many documents, no collision).

    Inherits the empty `dialect_hint` from SqlStore: corpus columns are typed
    DOUBLE/DATE, so queries are ANSI-clean and need no dialect guidance.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def available(self) -> str | None:
        if not os.path.exists(self.db_path):
            return f"No corpus DB at {self.db_path}. Run scripts/pdf_insight/pdf_to_duckdb.py first."
        return None

    def _connect_readonly(self):
        return duckdb.connect(self.db_path, read_only=True)

    def ingest_pdf(self, pdf_path: str, report_date: str | None = None,
                   strategy: str = "lines", title_for=None) -> dict:
        """Add (or replace) ONE PDF in the corpus as its own set of tables.

        `report_date` defaults to a date parsed from the filename, else today.
        `title_for(index) -> str` supplies table titles (the CLI passes goldens);
        runtime uploads omit it and get 'Table N'. Idempotent per document
        (doc_id = filename): re-ingesting the same name replaces that document's
        tables, never another's.
        """
        filename = os.path.basename(pdf_path)
        doc_id = filename
        slug = _doc_slug(doc_id)
        report_date = report_date or _report_date(filename) or dt.date.today().isoformat()
        tables = pdf.extract_tables(pdf_path, strategy=strategy)
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        con = duckdb.connect(self.db_path)  # read-write
        try:
            _ensure_registries(con)
            _drop_all_views(con)  # free the physical tables for replacement
            # Replace any prior ingest of THIS document (and only this one).
            for (phys,) in con.execute(
                "SELECT phys_table FROM doc_tables WHERE doc_id = ?", [doc_id]
            ).fetchall():
                con.execute(f'DROP TABLE IF EXISTS "{phys}"')
            con.execute("DELETE FROM doc_tables WHERE doc_id = ?", [doc_id])

            now = dt.datetime.now().isoformat(timespec="seconds")
            n_rows = sum(
                _ingest_table(con, t, report_date, filename, doc_id, slug, title_for)
                for t in tables
            )
            n_pages = max((t["page"] for t in tables), default=0)
            con.execute("DELETE FROM documents WHERE doc_id = ?", [doc_id])
            con.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                        [doc_id, filename, report_date, n_pages, len(tables), now])
            _rebuild_views(con)
        finally:
            con.close()
        return {"status": "success", "file": filename, "date": report_date,
                "tables": len(tables), "rows": n_rows}

    def list_schema(self) -> dict:
        """List what's queryable: the family views (each a union across the docs
        that share its shape) + report coverage. The corpus agent calls this FIRST,
        then queries a view by its `table` name."""
        unavailable = self.available()
        if unavailable:
            return {"status": "error", "error_message": unavailable}
        con = self._connect_readonly()
        try:
            fams = con.execute("""
                SELECT f.family_id, f.view_name, f.title, f.columns,
                       COUNT(DISTINCT dt.doc_id) AS n_docs
                FROM families f JOIN doc_tables dt ON dt.family_id = f.family_id
                GROUP BY f.family_id, f.view_name, f.title, f.columns
                ORDER BY f.family_id
            """).fetchall()
            n_docs, d_min, d_max = con.execute(
                "SELECT COUNT(*), MIN(report_date), MAX(report_date) FROM documents"
            ).fetchone()
            tables = [
                {"family": r[0], "table": r[1], "title": r[2],
                 "columns": json.loads(r[3]), "documents": r[4]}
                for r in fams
            ]
        except duckdb.Error as e:  # noqa: BLE001 - malformed/empty DB -> error dict
            return {"status": "error", "error_message": f"Corpus DB error: {e}"}
        finally:
            con.close()
        return {
            "status": "success",
            "documents": {"count": n_docs, "from": jsonable(d_min), "to": jsonable(d_max)},
            "note": "Each entry under `tables` is a VIEW (fam_*) that unions every "
                    "ingested document sharing that table's shape — query it by its "
                    "`table` name. Filter by report_date for one report or GROUP BY "
                    "report_date for a trend; exclude each table's subtotal with "
                    "`WHERE NOT is_total` before SUM/AVG.",
            "tables": tables,
        }

# The corpus function tools live in corpus_tools.py now — backend-neutral, so they
# pick DuckDB or Postgres by config instead of hardcoding this engine.
