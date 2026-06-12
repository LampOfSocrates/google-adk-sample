"""Pure PDF helpers on pdfplumber. No ADK, no LLM, no network — plain functions.

Table detection is deterministic, so it lives here in the tool layer, not an LlmAgent.
`extract_tables` flattens all pages into one list with a stable global `index`, so
callers can refer to "table 0, table 2" regardless of page.
"""
from __future__ import annotations

import pdfplumber


def _clean(cell) -> str:
    return ("" if cell is None else str(cell)).replace("\n", " ").strip()


def _sanitize_header(raw_header, ncols) -> list[str]:
    """Turn a messy detected header row into safe, unique column names.

    pdfplumber headers have blanks/dupes/merged cells. Empties become col_N and
    dupes get suffixed, so downstream (incl. SQLite columns) gets a clean list.
    """
    names, seen = [], {}
    for i in range(ncols):
        base = _clean(raw_header[i]) if raw_header and i < len(raw_header) else ""
        if not base:
            base = f"col_{i}"
        base = base.replace(" ", "_")  # identifier-ish but still readable
        name = base
        while name in seen:
            seen[base] += 1
            name = f"{base}_{seen[base]}"
        seen.setdefault(base, 0)
        names.append(name)
    return names


def extract_tables(path: str, select=None, strategy: str = "lines") -> list[dict]:
    """Detect tables in a PDF and return them as structured dicts.

    Args:
        path: PDF file path.
        select: global table indices to keep; None keeps all.
        strategy: 'lines' for ruled tables (default), 'text' for borderless ones.

    Returns:
        List of {index, page, header, rows, ncols}. `header` is sanitized; `rows`
        excludes the header, each cell a stripped string.
    """
    settings = {"vertical_strategy": strategy, "horizontal_strategy": strategy}
    tables: list[dict] = []
    index = 0
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for raw in page.find_tables(table_settings=settings):
                grid = raw.extract()
                if not grid:
                    index += 1
                    continue
                ncols = max((len(r) for r in grid), default=0)
                header = _sanitize_header(grid[0], ncols)
                rows = [[_clean(c) for c in r] for r in grid[1:]]
                if select is None or index in select:
                    tables.append(
                        {"index": index, "page": page_no, "header": header,
                         "rows": rows, "ncols": ncols}
                    )
                index += 1
    return tables


def tables_as_text(tables: list[dict]) -> str:
    """Render extracted tables to a flat, model-friendly text block.

    What the LLM_GETS_*_TABLES_AS_TEXT modes feed the model — a compact text view,
    not the raw PDF.
    """
    if not tables:
        return "(no tables detected)"
    chunks = []
    for t in tables:
        lines = [f"### Table {t['index']} (page {t['page']})",
                 " | ".join(t["header"])]
        lines += [" | ".join(r) for r in t["rows"]]
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)
