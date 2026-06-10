"""DuckDB backend — the whole-corpus store for LLM_QUERIES_CORPUS.

Reads the PERSISTENT, multi-PDF DuckDB corpus, so a question can span every
ingested report. Schema is richer than SQLite's: a `pdf_tables` registry maps
table index -> title/columns and a `documents` table records report coverage.

Queries open the DB `read_only`; `ingest_pdf` opens it read-write to APPEND one
report (idempotent per report_date). The same ingestion runs at runtime (upload)
and from the offline CLI (scripts/pdf_insight/pdf_to_duckdb.py), which now just calls this.

This module is just the `DuckDBStore` engine; the backend-neutral corpus tools
live in `corpus_tools.py` and pick this store (or Postgres) by config.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import duckdb

from shared import pdf_extractor as pdf

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
    must be lower_snake and identical report to report. See the SQLite version for
    the full rationale; the two are kept separate on purpose."""
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


def _ensure_registries(con) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            doc_id VARCHAR PRIMARY KEY, filename VARCHAR, report_date DATE,
            n_pages INTEGER, n_tables INTEGER, ingested_at TIMESTAMP
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS pdf_tables (
            table_index INTEGER PRIMARY KEY, table_name VARCHAR, title VARCHAR,
            columns VARCHAR, n_rows INTEGER
        )""")


def _ingest_table(con, table: dict, report_date: str, source_file: str,
                  title_for, now: str) -> int:
    """Create-if-needed and append one extracted table into t<index>."""
    idx = table["index"]
    tname = f"t{idx:02d}"
    cols = _columns_for(table)
    ncols = len(cols)
    numeric = _numeric_mask(table, ncols)
    coldefs = ", ".join(
        f'"{c}" {"DOUBLE" if numeric[i] else "VARCHAR"}' for i, c in enumerate(cols)
    )
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS "{tname}" (
            report_date DATE, source_file VARCHAR, row_index INTEGER,
            is_total BOOLEAN, {coldefs}
        )""")
    con.execute(f'DELETE FROM "{tname}" WHERE report_date = ?', [report_date])  # idempotent
    placeholders = ", ".join("?" for _ in range(4 + ncols))
    for ri, raw in enumerate(table["rows"]):
        is_total = bool(raw and str(raw[0]).strip().lower() == "total")
        vals = [
            _to_num(raw[c] if c < len(raw) else "") if numeric[c]
            else (raw[c] if c < len(raw) else "")
            for c in range(ncols)
        ]
        con.execute(f'INSERT INTO "{tname}" VALUES ({placeholders})',
                    [report_date, source_file, ri, is_total, *vals])
    title = title_for(idx) if title_for else f"Table {idx}"
    total_rows = con.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    con.execute("""
        INSERT INTO pdf_tables (table_index, table_name, title, columns, n_rows)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (table_index) DO UPDATE SET
            title = excluded.title, columns = excluded.columns, n_rows = excluded.n_rows
    """, [idx, tname, title, json.dumps(cols), total_rows])
    return len(table["rows"])


class DuckDBStore(SqlStore):
    """The single whole-corpus DuckDB database (multi-report corpus).

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
        """Append one PDF's tables to the corpus (read-write; idempotent per date).

        `report_date` defaults to a date parsed from the filename, else today.
        `title_for(index) -> str` supplies table titles (the CLI passes goldens);
        runtime uploads omit it and get 'Table N'.
        """
        filename = os.path.basename(pdf_path)
        report_date = report_date or _report_date(filename) or dt.date.today().isoformat()
        tables = pdf.extract_tables(pdf_path, strategy=strategy)
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        con = duckdb.connect(self.db_path)  # read-write
        try:
            _ensure_registries(con)
            now = dt.datetime.now().isoformat(timespec="seconds")
            n_rows = sum(_ingest_table(con, t, report_date, filename, title_for, now)
                         for t in tables)
            n_pages = max((t["page"] for t in tables), default=0)
            con.execute("DELETE FROM documents WHERE doc_id = ?", [filename])
            con.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                        [filename, filename, report_date, n_pages, len(tables), now])
        finally:
            con.close()
        return {"status": "success", "file": filename, "date": report_date,
                "tables": len(tables), "rows": n_rows}

    def list_schema(self) -> dict:
        """List what's queryable: the table registry + report coverage. Call FIRST."""
        unavailable = self.available()
        if unavailable:
            return {"status": "error", "error_message": unavailable}
        con = self._connect_readonly()
        try:
            reg = con.execute(
                "SELECT table_index, table_name, title, columns FROM pdf_tables "
                "ORDER BY table_index"
            ).fetchall()
            n_docs, d_min, d_max = con.execute(
                "SELECT COUNT(*), MIN(report_date), MAX(report_date) FROM documents"
            ).fetchone()
            tables = [
                {"index": r[0], "table": r[1], "title": r[2], "columns": json.loads(r[3])}
                for r in reg
            ]
        except duckdb.Error as e:  # noqa: BLE001 - malformed/empty DB -> error dict
            return {"status": "error", "error_message": f"Corpus DB error: {e}"}
        finally:
            con.close()
        return {
            "status": "success",
            "documents": {"count": n_docs, "from": jsonable(d_min), "to": jsonable(d_max)},
            "note": "Each tNN accumulates rows across weeks (filter/GROUP BY report_date). "
                    "Exclude each table's subtotal with `WHERE NOT is_total` before SUM/AVG.",
            "tables": tables,
        }

# The corpus function tools live in corpus_tools.py now — backend-neutral, so they
# pick DuckDB or Postgres by config instead of hardcoding this engine.
