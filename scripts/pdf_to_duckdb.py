"""Ingest the weekly PDF report corpus into a local DuckDB (offline batch).

PIPELINE: scripts/weekly_report.py drops dated PDFs into tests/pdf/samples/; this
CLI parses every table out of them and lands them in one DuckDB so the corpus
mode can answer questions across the whole corpus — including week-over-week
trends.

    python scripts/pdf_to_duckdb.py                       # ingest tests/pdf/samples
    python scripts/pdf_to_duckdb.py --reset               # rebuild from scratch
    python scripts/pdf_to_duckdb.py --query "SELECT ..."  # ad-hoc check

The actual ingestion (the canonical documents/pdf_tables/tNN schema) lives in
`DuckDBStore.ingest_pdf` — the SAME code path the runtime upload handler uses, so
the offline corpus and an uploaded one are built identically. This script just
batches a folder and supplies table titles from the sibling .golden.json files.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb  # noqa: E402 - only for --query

from apps.pdf_insight import storage  # noqa: E402
from apps.pdf_insight.stores import DuckDBStore  # noqa: E402

SAMPLES_DIR = os.path.join("tests", "pdf", "samples")
# Same default the reader resolves, so build + query agree and both honour
# PDF_CORPUS_DB / PDF_DATA_DIR.
DB_PATH = storage.duckdb_dsn()


def _golden_titles(pdf_path: str):
    """Build a title_for(index) -> str from a sibling .golden.json, else None."""
    gp = pdf_path.rsplit(".pdf", 1)[0] + ".golden.json"
    if not os.path.exists(gp):
        return None
    golden = json.load(open(gp, encoding="utf-8"))
    by_index = {t["index"]: t["title"] for t in golden.get("tables", [])}
    return lambda idx: by_index.get(idx, f"Table {idx}")


def ingest_dir(db_path: str, samples_dir: str, pattern: str = "*.pdf",
               strategy: str = "lines", reset: bool = False) -> list[dict]:
    """Ingest every dated PDF in a folder into one corpus DuckDB, via DuckDBStore."""
    if reset and os.path.exists(db_path):
        os.remove(db_path)
    store = DuckDBStore(db_path)
    files = sorted(glob.glob(os.path.join(samples_dir, pattern)))
    return [store.ingest_pdf(f, strategy=strategy, title_for=_golden_titles(f))
            for f in files]


def _main() -> None:
    p = argparse.ArgumentParser(description="Ingest the PDF report corpus into DuckDB.")
    p.add_argument("--dir", default=SAMPLES_DIR, help="corpus folder of dated PDFs")
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
