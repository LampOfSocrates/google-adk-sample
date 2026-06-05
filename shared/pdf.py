"""Pure, deterministic PDF helpers built on pdfplumber. No ADK, no LLM, no network.

ADK idiom note: everything in this module is a plain function, NOT an agent.
Table detection is fully deterministic, so it belongs in the *tool* layer that an
agent calls — never wrap deterministic work in an LlmAgent (it only adds cost and
nondeterminism). The agents in apps/pdf_insight import these and expose them as
function tools.

`extract_tables` flattens every detected table across all pages into one list and
gives each a stable global `index`, so callers (and the LLM) can refer to "table 0,
table 2" regardless of which page they sit on.
"""
from __future__ import annotations

import pdfplumber


def _clean(cell) -> str:
    return ("" if cell is None else str(cell)).replace("\n", " ").strip()


def _sanitize_header(raw_header, ncols) -> list[str]:
    """Turn a detected header row into safe, unique column names.

    pdfplumber headers are messy (blanks, duplicates, merged cells). We fall back
    to col_N for empties and de-duplicate by suffixing, so downstream code (incl.
    SQLite column names) always gets a clean, collision-free list.
    """
    names, seen = [], {}
    for i in range(ncols):
        base = _clean(raw_header[i]) if raw_header and i < len(raw_header) else ""
        if not base:
            base = f"col_{i}"
        # collapse to identifier-ish: keep it readable but predictable
        base = base.replace(" ", "_")
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
        select: optional list of global table indices to keep (others dropped).
            None -> keep every detected table.
        strategy: 'lines' for ruled tables (default), 'text' for borderless ones.

    Returns:
        A list of {index, page, header, rows, ncols}. `header` is sanitized;
        `rows` are the data rows (header excluded), each cell a stripped string.
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

    This is what the `LLM_GETS_ALL_TABLES_AS_TEXT` / `LLM_GETS_SOME_TABLES_AS_TEXT`
    modes feed to the model — a compact textual view, not the raw PDF.
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
