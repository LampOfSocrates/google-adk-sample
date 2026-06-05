"""Free, no-API inventory of the tables pdfplumber can detect in any PDF.

Run this first on a new PDF to see what the deterministic detector finds —
how many tables, where, their shape, and a preview — before spending any
Gemini quota on structuring them.

Usage:
    python scripts/inspect_pdf.py path/to/file.pdf
    python scripts/inspect_pdf.py path/to/file.pdf --strategy text   # borderless tables
"""
import argparse

import pdfplumber


def _preview(rows, max_rows=2, max_cols=6):
    lines = []
    for row in rows[:max_rows]:
        cells = [
            ("" if c is None else str(c).replace("\n", " ")).strip()[:18]
            for c in row[:max_cols]
        ]
        lines.append(" | ".join(cells))
    if len(rows) > max_rows:
        lines.append(f"... (+{len(rows) - max_rows} more rows)")
    return lines


def inspect(path, strategy="lines"):
    settings = {"vertical_strategy": strategy, "horizontal_strategy": strategy}
    total = 0
    with pdfplumber.open(path) as pdf:
        print(f"{path}: {len(pdf.pages)} page(s), strategy='{strategy}'\n")
        for page_no, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables(table_settings=settings)
            if not tables:
                continue
            print(f"Page {page_no}: {len(tables)} table(s) detected")
            for t_no, table in enumerate(tables, start=1):
                rows = table.extract()
                ncols = max((len(r) for r in rows), default=0)
                x0, top, x1, bottom = table.bbox
                print(
                    f"  [{page_no}.{t_no}] {len(rows)} rows x {ncols} cols  "
                    f"bbox=({x0:.0f},{top:.0f},{x1:.0f},{bottom:.0f})"
                )
                for line in _preview(rows):
                    print(f"        {line}")
                total += 1
            print()
        print(f"TOTAL: {total} table(s) detected across the document")
        print(
            "\nNote: counts/shapes are pdfplumber's best guess. Messy tables "
            "(merged cells, multi-row headers, borderless) may be miscounted — "
            "try --strategy text, and expect to LLM-structure the hard ones."
        )


def main():
    ap = argparse.ArgumentParser(
        description="Inventory tables pdfplumber detects in a PDF (no API calls)."
    )
    ap.add_argument("pdf", help="path to the PDF")
    ap.add_argument(
        "--strategy",
        choices=["lines", "text"],
        default="lines",
        help="'lines' for ruled tables (default), 'text' for borderless ones",
    )
    args = ap.parse_args()
    inspect(args.pdf, strategy=args.strategy)


if __name__ == "__main__":
    main()
