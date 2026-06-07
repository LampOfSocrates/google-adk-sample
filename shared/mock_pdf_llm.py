"""A pdf_insight-aware offline LLM — `MockLlm` that actually answers risk questions.

The generic `MockLlm` runs the agent graph offline but is too dumb for pdf_insight:
in SQL mode it just does `SELECT * ... LIMIT 5` and echoes the raw rows. This
subclass *knows the domain* (a derivatives-desk risk report: Greeks delta/gamma/
vega/theta/rho + notional/var/pnl, sliced by region/asset/desk/underlying/tenor/
currency/date) and so produces **golden-accurate** offline behavior:

  * SQL mode (LLM_MAKES_SQL_FROM_CHAT) and stash mode (LLM_QUERIES_STASH):
      read the schema returned by list_sql_schema / list_stash_schema, parse the
      question into (measure, dimension, aggregation, filters), emit a real
      read-only SELECT, and then phrase the returned rows into a sentence. The
      tool runs the SQL for real, so the numbers are the true numbers.
  * tables-as-text modes + the auto router: parse the injected `tables_as_text`
      block (or the extract_tables result) and compute the answer in Python.

It is heuristic, not a model: intent is matched against domain synonym/value
vocab. Unrecognized requests fall back to the generic `MockLlm`. Used explicitly
by the pdf_insight tests — the default `LLM_BACKEND=mock` stays the plain mock.
"""
from __future__ import annotations

import re
from typing import AsyncGenerator

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from .mock_llm import (
    MockLlm,
    _call_part,
    _current_turn,
    _text_part,
)

# question word -> canonical measure key (the key is also the column substring).
_MEASURE_SYNONYMS = {
    "vega": "vega", "delta": "delta", "gamma": "gamma", "theta": "theta",
    "rho": "rho", "notional": "notional",
    "var": "var", "value at risk": "var", "value-at-risk": "var",
    "pnl": "pnl", "p&l": "pnl", "p & l": "pnl", "profit": "pnl",
    "position": "pos", "positions": "pos", "how many": "pos",
}
# display label for an answer sentence.
_MEASURE_LABEL = {"var": "VaR", "pnl": "P&L", "pos": "positions"}
# question word -> (dimension key, column substring used to find the column).
_DIM_SYNONYMS = {
    "region": ("region", "region"),
    "asset class": ("asset_class", "asset"), "asset": ("asset_class", "asset"),
    "desk": ("desk", "desk"),
    "underlying": ("underlying", "underlying"), "ticker": ("underlying", "underlying"),
    "tenor": ("tenor", "tenor"), "maturity": ("tenor", "tenor"),
    "currency": ("currency", "currency"), "ccy": ("currency", "currency"),
    "book": ("book", "book"),
}
# known dimension VALUES, so "Americas vega" / "SPX" / "Rates desk" become filters.
# Priority order disambiguates a value living in two dims (e.g. "Rates").
_VALUE_VOCAB = [
    ("region", ["Americas", "EMEA", "APAC"]),
    ("currency", ["USD", "EUR", "GBP", "JPY"]),
    ("underlying", ["SPX", "ESTOXX", "NKY", "FTSE", "UST10Y", "Bund", "JGB10Y",
                    "Gilt", "EUR/USD", "USD/JPY", "GBP/USD", "CDX-IG",
                    "iTraxx-Main", "WTI", "Gold", "Brent"]),
    ("tenor", ["0-1M", "1-3M", "3-6M", "6-12M", "1Y+"]),
    ("asset_class", ["Equity", "Rates", "FX", "Credit", "Commodity"]),
    ("desk", ["Flow Equity", "Exotics", "Rates", "FX Options", "Credit"]),
]
_TREND_WORDS = ("trend", "over time", "weekly", "by week", "each week", "history",
                "historical", "week over week", "week-over-week")
_AVG_WORDS = ("average", "avg", "mean")
_MAX_WORDS = ("most", "highest", "largest", "biggest", "max", "maximum", "top")
_MIN_WORDS = ("least", "lowest", "smallest", "min", "minimum", "most negative")


def _fmt_num(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{int(v):,}" if v.is_integer() else f"{v:,.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _find_col(columns, substr: str):
    """First column whose name contains `substr` (case-insensitive), else None."""
    for c in columns:
        if substr in c.lower():
            return c
    return None


def _parse_intent(text: str) -> dict:
    """Turn a question into {measure, dim, dim_col_key, agg, order, limit, filters}."""
    low = text.lower()
    measure = next((m for phrase, m in _MEASURE_SYNONYMS.items() if phrase in low), None)

    dim = dim_key = None
    for phrase, (d, colkey) in _DIM_SYNONYMS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", low):
            dim, dim_key = d, colkey
            break

    trend = any(w in low for w in _TREND_WORDS)
    agg = "avg" if any(w in low for w in _AVG_WORDS) else "sum"

    order = limit = None
    m = re.search(r"top\s+(\d+)", low)
    if m:
        order, limit = "desc", int(m.group(1))
    elif any(w in low for w in _MIN_WORDS):
        order, limit = "asc", 1
    elif any(w in low for w in _MAX_WORDS):
        order, limit = "desc", 1

    # value filters (e.g. region='Americas'); dedupe a value shared across dims.
    filters, claimed = {}, set()
    for d, vals in _VALUE_VOCAB:
        for v in vals:
            if v.lower() in low and v.lower() not in claimed:
                filters[d] = v
                claimed.add(v.lower())
    return {"measure": measure, "dim": dim, "dim_col_key": dim_key, "agg": agg,
            "order": order, "limit": limit, "trend": trend, "filters": filters}


# ---------------------------------------------------------------- SQL modes ---
def _normalize_schema(fr) -> list[dict]:
    """list_sql_schema / list_stash_schema response -> [{name, columns}]."""
    r = fr.response if isinstance(fr.response, dict) else {}
    if "tables" in r:  # list_stash_schema
        return [{"name": t["table"], "columns": t.get("columns", [])} for t in r["tables"]]
    if "schema" in r:  # list_sql_schema
        return [{"name": t["table"], "columns": t.get("columns", [])} for t in r["schema"]]
    return []


def _pick_table(schema, measure_key, dim_col_key):
    cands = [t for t in schema if _find_col(t["columns"], measure_key)]
    if not cands:
        return None
    if dim_col_key:
        both = [t for t in cands if _find_col(t["columns"], dim_col_key)]
        if both:
            return both[0]
    return cands[0]


def _build_sql(intent: dict, schema: list[dict], is_stash: bool):
    """Return (sql, shape) or (None, None) if the intent can't be expressed."""
    meas = intent["measure"]
    if not meas:
        return None, None
    table = _pick_table(schema, meas, intent["dim_col_key"])
    if not table:
        return None, None
    cols, name = table["columns"], table["name"]
    mcol = _find_col(cols, meas)
    # Stash columns are already DOUBLE; the single-PDF SQLite stores cells as TEXT
    # WITH thousands commas ("2,765"), and SQLite SUM would coerce that to 2 — so
    # strip the commas and CAST before aggregating.
    mexpr = f'"{mcol}"' if is_stash else f'CAST(REPLACE("{mcol}", \',\', \'\') AS REAL)'

    # exclude each table's own subtotal: stash has a boolean flag; sqlite doesn't.
    label_col = cols[0] if cols else None
    excl = "NOT is_total" if is_stash else (f'"{label_col}" <> \'Total\'' if label_col else "1=1")

    _FILTER_COLKEY = {"asset_class": "asset"}  # dim key -> column substring
    wheres = [excl]
    for d, v in intent["filters"].items():
        col = _find_col(cols, _FILTER_COLKEY.get(d, d))
        if col:
            wheres.append(f"\"{col}\" = '{v}'")
    where = " AND ".join(wheres)
    agg = "AVG" if intent["agg"] == "avg" else "SUM"

    if intent["trend"] and is_stash:
        return (f'SELECT report_date, {agg}({mexpr}) AS {meas} FROM "{name}" '
                f"WHERE {where} GROUP BY report_date ORDER BY report_date"), "trend"

    dim_col = _find_col(cols, intent["dim_col_key"]) if intent["dim_col_key"] else None
    if dim_col:
        order = intent["order"] or "desc"
        limit = f" LIMIT {intent['limit']}" if intent["limit"] else ""
        return (f'SELECT "{dim_col}", {agg}({mexpr}) AS {meas} FROM "{name}" '
                f'WHERE {where} GROUP BY "{dim_col}" ORDER BY {meas} {order}{limit}'), \
               ("extreme" if intent["limit"] == 1 else "grouped")

    return f'SELECT {agg}({mexpr}) AS {meas} FROM "{name}" WHERE {where}', "scalar"


def _format_rows(intent: dict, result: dict) -> str:
    rows = result.get("rows") or []
    disp = _MEASURE_LABEL.get(intent["measure"], intent["measure"] or "value")
    flt = "".join(f" for {v}" for v in intent["filters"].values())
    if not rows:
        return f"No {disp} found{flt}."
    if len(rows) == 1 and len(rows[0]) == 1:  # scalar
        word = "Average" if intent["agg"] == "avg" else "Total"
        return f"{word} {disp}{flt} is {_fmt_num(rows[0][0])}."
    if intent["trend"]:
        seq = " → ".join(f"{r[0]}: {_fmt_num(r[1])}" for r in rows)
        return f"{disp.capitalize()}{flt} by week — {seq}."
    if intent["limit"] == 1 and len(rows[0]) >= 2:
        sup = "lowest" if intent["order"] == "asc" else "highest"
        return f"{rows[0][0]} has the {sup} {disp} ({_fmt_num(rows[0][1])})."
    body = ", ".join(f"{r[0]} {_fmt_num(r[1])}" for r in rows[:8])
    return f"{disp.capitalize()} by {intent['dim']}: {body}."


# ----------------------------------------------------------- tables-as-text ---
_TABLE_HDR = re.compile(r"^###\s*Table\s+(\d+)", re.IGNORECASE)


def _gather_prompt_text(req: LlmRequest) -> str:
    """All text the agent was given: system instruction + every content part."""
    chunks = []
    cfg = getattr(req, "config", None)
    si = getattr(cfg, "system_instruction", None) if cfg else None
    if isinstance(si, str):
        chunks.append(si)
    elif si is not None:
        for p in getattr(si, "parts", None) or []:
            if getattr(p, "text", None):
                chunks.append(p.text)
    for content in req.contents or []:
        for p in content.parts or []:
            if getattr(p, "text", None):
                chunks.append(p.text)
    return "\n".join(chunks)


def _parse_tables_text(text: str) -> list[dict]:
    """Parse a `tables_as_text` block into [{index, header, rows}]."""
    tables, cur = [], None
    for line in text.splitlines():
        m = _TABLE_HDR.match(line.strip())
        if m:
            cur = {"index": int(m.group(1)), "header": None, "rows": []}
            tables.append(cur)
        elif cur is not None and "|" in line:
            cells = [c.strip() for c in line.split("|")]
            if cur["header"] is None:
                cur["header"] = cells
            else:
                cur["rows"].append(cells)
    return [t for t in tables if t["header"]]


def _answer_from_tables(intent: dict, tables: list[dict]) -> str | None:
    meas = intent["measure"]
    if not meas:
        return None
    # pick the table that has the measure (and the dimension, if asked).
    chosen = None
    for t in tables:
        if _find_col(t["header"], meas):
            if not intent["dim_col_key"] or _find_col(t["header"], intent["dim_col_key"]):
                chosen = t
                break
            chosen = chosen or t
    if not chosen:
        return None
    hdr = chosen["header"]
    mi = hdr.index(_find_col(hdr, meas))
    data = [r for r in chosen["rows"] if r and r[0].strip().lower() != "total"]

    def num(cell):
        try:
            return float(cell.replace(",", "").replace("%", ""))
        except (ValueError, AttributeError):
            return None

    disp = _MEASURE_LABEL.get(meas, meas)
    if intent["dim_col_key"]:
        di = hdr.index(_find_col(hdr, intent["dim_col_key"]))
        pairs = [(r[di], num(r[mi])) for r in data if mi < len(r) and num(r[mi]) is not None]
        if not pairs:
            return None
        if intent["limit"] == 1:
            best = (min if intent["order"] == "asc" else max)(pairs, key=lambda p: p[1])
            sup = "lowest" if intent["order"] == "asc" else "highest"
            return f"{best[0]} has the {sup} {disp} ({_fmt_num(best[1])})."
        pairs.sort(key=lambda p: p[1], reverse=(intent["order"] != "asc"))
        body = ", ".join(f"{a} {_fmt_num(b)}" for a, b in pairs[:8])
        return f"{disp.capitalize()} by {intent['dim']}: {body}."
    vals = [num(r[mi]) for r in data if mi < len(r) and num(r[mi]) is not None]
    if not vals:
        return None
    total = sum(vals) / len(vals) if intent["agg"] == "avg" else sum(vals)
    word = "Average" if intent["agg"] == "avg" else "Total"
    return f"{word} {disp} is {_fmt_num(total)}."


class MockPdfLlm(MockLlm):
    """Domain-aware offline mock for pdf_insight (see module docstring)."""

    model: str = "mock-pdf"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield self._decide_pdf(llm_request)

    def _decide_pdf(self, req: LlmRequest) -> LlmResponse:
        available = set((req.tools_dict or {}).keys())
        user_text, fr = _current_turn(req.contents)
        is_stash = "run_stash_sql" in available

        # 1) a tool just returned -----------------------------------------------
        if fr is not None and fr.name != "transfer_to_agent":
            if fr.name in ("list_sql_schema", "list_stash_schema"):
                schema = _normalize_schema(fr)
                sql, _ = _build_sql(_parse_intent(user_text), schema, is_stash)
                run_tool = "run_stash_sql" if is_stash else "run_sql"
                if sql:
                    return self._log(f"{run_tool}:domain", _call_part(run_tool, {"query": sql}))
                # couldn't express it -> let the base mock's generic SELECT run.
                return super()._decide(req)
            if fr.name in ("run_sql", "run_stash_sql"):
                result = fr.response if isinstance(fr.response, dict) else {}
                if result.get("status") == "success":
                    return self._log("answer:sql", _text_part(_format_rows(_parse_intent(user_text), result)))
                return super()._decide(req)
            if fr.name == "extract_tables":
                r = fr.response if isinstance(fr.response, dict) else {}
                tables = _parse_tables_text(r.get("text", ""))
                ans = _answer_from_tables(_parse_intent(user_text), tables)
                if ans:
                    return self._log("answer:extract", _text_part(ans))
                return super()._decide(req)

        # 2) no tool yet: pick the first action for whichever agent this is -----
        if "list_stash_schema" in available:
            return self._log("list_stash_schema", _call_part("list_stash_schema", {}))
        if "list_sql_schema" in available:
            return self._log("list_sql_schema", _call_part("list_sql_schema", {}))
        if "extract_tables" in available:
            return self._log("extract_tables", _call_part("extract_tables", {}))

        # 3) tables-as-text answerer: no tools, tables injected into the prompt --
        if fr is None and not available:
            tables = _parse_tables_text(_gather_prompt_text(req))
            if tables:
                ans = _answer_from_tables(_parse_intent(user_text), tables)
                if ans:
                    return self._log("answer:text", _text_part(ans))

        # 4) anything else -> the generic mock --------------------------------
        return super()._decide(req)
