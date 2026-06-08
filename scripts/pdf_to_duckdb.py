"""Ingest the weekly PDF report stash into a local DuckDB.

PIPELINE: scripts/weekly_report.py drops dated PDFs into tests/pdf/samples/; this
CLI parses every table out of them (pdfplumber, via shared.pdf) and lands them in
one DuckDB file so the pdf_insight SQL mode (LLM_MAKES_SQL_FROM_CHAT) can answer
questions across the whole stash — including week-over-week trends.

    python scripts/pdf_to_duckdb.py                       # ingest tests/pdf/samples
    python scripts/pdf_to_duckdb.py --reset               # rebuild from scratch
    python scripts/pdf_to_duckdb.py --query "SELECT ..."  # ad-hoc check

SCHEMA. Every weekly report has the SAME 16 tables, so each logical table
accumulates across weeks into ONE physical table keyed by report_date:

  documents(doc_id, filename, report_date, n_pages, n_tables, ingested_at)
  pdf_tables(table_index, table_name, title, columns, n_rows)   -- registry
  t00 .. t15   -- one per table index; columns:
      report_date DATE, source_file VARCHAR, row_index INT, is_total BOOLEAN,
      <data columns>   (col 0 = text label; numeric columns parsed to DOUBLE)

`is_total` flags the table's own "Total" row so Text2SQL can exclude it from
SUM()s. The registry (`pdf_tables`) is what the agent reads first — the index->title
map — exactly like list_sql_schema in apps/pdf_insight/stores/sqlite_store.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402

from apps.pdf_insight import storage  # noqa: E402
from shared import pdf  # noqa: E402

SAMPLES_DIR = os.path.join("tests", "pdf", "samples")
# Same default the reader (duckdb_store) resolves, so build + query agree and
# both honour PDF_STASH_DB / PDF_DATA_DIR.
DB_PATH = storage.duckdb_dsn()
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# ------------------------------------------------------------------ helpers ---
def _report_date(filename: str) -> str | None:
    m = _DATE_RE.search(filename)
    return m.group(1) if m else None


def _sql_ident(name: str, fallback: str) -> str:
    """Header cell -> safe, lower-snake DuckDB identifier. 'Vega($k)' -> 'vega_k'."""
    ident = re.sub(r"\W+", "_", name).strip("_").lower()
    if not ident or ident[0].isdigit():
        ident = f"c_{ident}" if ident else fallback
    return ident


def _to_num(cell: str):
    """Parse '9,334' / '-955' -> float, else None. '%' and blanks -> None."""
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
    """De-duplicated, SQL-safe column names for an extracted table."""
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
    """A column is numeric if every non-empty data cell in it parses as a number.
    Column 0 (the row label) is always treated as text."""
    numeric = [True] * ncols
    numeric[0] = False
    for r in table["rows"]:
        for c in range(1, ncols):
            cell = r[c] if c < len(r) else ""
            if cell and _to_num(cell) is None:
                numeric[c] = False
    return numeric


# ----------------------------------------------------------------- ingest ----
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


def _title_for(index: int, golden: dict | None) -> str:
    if golden:
        for t in golden.get("tables", []):
            if t["index"] == index:
                return t["title"]
    return f"Table {index}"


def _ingest_table(con, table: dict, report_date: str, source_file: str,
                  golden: dict | None, now: str) -> int:
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
    # Idempotent: drop any prior rows for this date before re-inserting.
    con.execute(f'DELETE FROM "{tname}" WHERE report_date = ?', [report_date])

    placeholders = ", ".join("?" for _ in range(4 + ncols))
    for ri, raw in enumerate(table["rows"]):
        is_total = bool(raw and str(raw[0]).strip().lower() == "total")
        vals = []
        for c in range(ncols):
            cell = raw[c] if c < len(raw) else ""
            vals.append(_to_num(cell) if numeric[c] else cell)
        con.execute(
            f'INSERT INTO "{tname}" VALUES ({placeholders})',
            [report_date, source_file, ri, is_total, *vals],
        )

    title = _title_for(idx, golden)
    total_rows = con.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
    con.execute("""
        INSERT INTO pdf_tables (table_index, table_name, title, columns, n_rows)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (table_index) DO UPDATE SET
            title = excluded.title, columns = excluded.columns, n_rows = excluded.n_rows
    """, [idx, tname, title, json.dumps(cols), total_rows])
    return len(table["rows"])


def ingest_pdf(con, path: str, strategy: str = "lines") -> dict:
    """Parse one PDF and load all its tables. Returns a per-file summary."""
    filename = os.path.basename(path)
    report_date = _report_date(filename) or dt.date.today().isoformat()
    golden_path = path.rsplit(".pdf", 1)[0] + ".golden.json"
    golden = json.load(open(golden_path, encoding="utf-8")) if os.path.exists(golden_path) else None

    tables = pdf.extract_tables(path, strategy=strategy)
    now = dt.datetime.now().isoformat(timespec="seconds")
    n_rows = sum(_ingest_table(con, t, report_date, filename, golden, now) for t in tables)
    n_pages = max((t["page"] for t in tables), default=0)

    con.execute("DELETE FROM documents WHERE doc_id = ?", [filename])
    con.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
        [filename, filename, report_date, n_pages, len(tables), now],
    )
    return {"file": filename, "date": report_date, "tables": len(tables), "rows": n_rows}


def ingest_dir(db_path: str, samples_dir: str, pattern: str = "*.pdf",
               strategy: str = "lines", reset: bool = False) -> list[dict]:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    if reset and os.path.exists(db_path):
        os.remove(db_path)
    con = duckdb.connect(db_path)
    try:
        _ensure_registries(con)
        files = sorted(glob.glob(os.path.join(samples_dir, pattern)))
        return [ingest_pdf(con, f, strategy) for f in files]
    finally:
        con.close()


# ------------------------------------------------------------------- main ----
def _main() -> None:
    p = argparse.ArgumentParser(description="Ingest the PDF report stash into DuckDB.")
    p.add_argument("--dir", default=SAMPLES_DIR, help="stash folder of dated PDFs")
    p.add_argument("--db", default=DB_PATH, help="DuckDB file to write")
    p.add_argument("--glob", default="*.pdf", help="filename pattern")
    p.add_argument("--strategy", default="lines", choices=["lines", "text"],
                   help="pdfplumber table strategy (lines=ruled, text=borderless)")
    p.add_argument("--reset", action="store_true", help="delete the DB first")
    p.add_argument("--query", help="run a SELECT against the DB and print rows")
    a = p.parse_args()

    if a.query:
        con = duckdb.connect(a.db)
        try:
            cur = con.execute(a.query)
            cols = [d[0] for d in cur.description]
            print(" | ".join(cols))
            for row in cur.fetchall():
                print(" | ".join("" if v is None else str(v) for v in row))
        finally:
            con.close()
        return

    summary = ingest_dir(a.db, a.dir, a.glob, a.strategy, a.reset)
    for s in summary:
        print(f"  {s['file']}: {s['tables']} tables, {s['rows']} rows  [{s['date']}]")
    print(f"ingested {len(summary)} report(s) -> {a.db}")


if __name__ == "__main__":
    _main()
