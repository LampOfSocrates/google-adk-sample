"""A pdf_insight-aware offline LLM — `MockLlm` that actually answers risk questions.

The generic `MockLlm` runs the agent graph offline but is too dumb for pdf_insight:
in SQL mode it just does `SELECT * ... LIMIT 5` and echoes the raw rows. This
subclass *knows the domain* (a derivatives-desk risk report whose tables carry the
Greeks delta/vega sliced by region) and so produces **golden-accurate** offline
behavior:

  * SQL mode (LLM_MAKES_SQL_FROM_CHAT) and corpus mode (LLM_QUERIES_CORPUS):
      read the schema returned by list_sql_schema / list_corpus_schema, parse the
      question into an intent (measure, dimension, filters, top/trend flags), emit
      a real read-only SELECT, and phrase the returned rows into a sentence. The
      tool runs the SQL for real, so the numbers are the true numbers.
  * tables-as-text modes + the auto router: parse the injected `tables_as_text`
      block (or the extract_tables result) and compute the answer in Python.

Scope is deliberately narrow: it answers exactly the intents the pdf_insight
golden tests assert — total/by-region/most/trend over vega and delta. The
vocabulary is fully table-driven (`_MEASURES`/`_DIMENSIONS`/`_VALUES` below): to
teach the mock a new word, add a row, not a branch. Anything it can't express
falls back to the generic `MockLlm`. Used only by the pdf_insight tests — the
default `LLM_BACKEND=mock` stays the plain mock.
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

# --- the whole question vocabulary, table-driven (extend a table, not the parser) ---
# measure phrase -> column substring (also used as the display label).
_MEASURES = {"vega": "vega", "delta": "delta"}
# dimension phrase -> column substring to GROUP BY.
_DIMENSIONS = {"region": "region"}
# filter phrase -> (column substring, properly-cased SQL value). Lets "Americas
# vega" become a WHERE clause.
_VALUES = {
    "americas": ("region", "Americas"),
    "emea": ("region", "EMEA"),
    "apac": ("region", "APAC"),
}
# phrases that flip a flag. Matched as substrings (so "trended" hits "trend").
_TREND_WORDS = ("trend", "over time", "over the weeks", "each week",
                "week over week", "week-over-week")
_TOP_WORDS = ("most", "highest", "largest", "biggest", "max", "maximum", "top")


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
    """Turn a question into {measure, dim, top, trend, filters} via the vocab tables."""
    low = text.lower()
    measure = next((col for phrase, col in _MEASURES.items() if phrase in low), None)
    dim = next((col for phrase, col in _DIMENSIONS.items()
                if re.search(rf"\b{re.escape(phrase)}\b", low)), None)
    filters = {col: value for phrase, (col, value) in _VALUES.items() if phrase in low}
    return {
        "measure": measure,
        "dim": dim,
        "top": any(w in low for w in _TOP_WORDS),
        "trend": any(w in low for w in _TREND_WORDS),
        "filters": filters,
    }


# ---------------------------------------------------------------- SQL modes ---
def _normalize_schema(fr) -> list[dict]:
    """list_sql_schema / list_corpus_schema response -> [{name, columns}]."""
    r = fr.response if isinstance(fr.response, dict) else {}
    if "tables" in r:  # list_corpus_schema
        return [{"name": t["table"], "columns": t.get("columns", [])} for t in r["tables"]]
    if "schema" in r:  # list_sql_schema
        return [{"name": t["table"], "columns": t.get("columns", [])} for t in r["schema"]]
    return []


def _pick_table(schema, measure_sub, dim_sub):
    """First table carrying the measure (and the dimension too, if one was asked)."""
    cands = [t for t in schema if _find_col(t["columns"], measure_sub)]
    if not cands:
        return None
    if dim_sub:
        both = [t for t in cands if _find_col(t["columns"], dim_sub)]
        if both:
            return both[0]
    return cands[0]


def _build_sql(intent: dict, schema: list[dict], is_corpus: bool) -> str | None:
    """A single read-only SELECT for the intent, or None if it can't be expressed."""
    meas = intent["measure"]
    if not meas:
        return None
    table = _pick_table(schema, meas, intent["dim"])
    if not table:
        return None
    cols, name = table["columns"], table["name"]
    mcol = _find_col(cols, meas)
    # Corpus columns are already DOUBLE; the single-PDF SQLite stores cells as TEXT
    # WITH thousands commas ("2,765"), and SQLite SUM would coerce that to 2 — so
    # strip the commas and CAST before aggregating.
    mexpr = f'"{mcol}"' if is_corpus else f'CAST(REPLACE("{mcol}", \',\', \'\') AS REAL)'

    # Exclude each table's own subtotal: corpus has a boolean flag; sqlite doesn't.
    label_col = cols[0] if cols else None
    excl = "NOT is_total" if is_corpus else (f'"{label_col}" <> \'Total\'' if label_col else "1=1")
    wheres = [excl]
    for col_sub, value in intent["filters"].items():
        col = _find_col(cols, col_sub)
        if col:
            wheres.append(f"\"{col}\" = '{value}'")
    where = " AND ".join(wheres)

    if intent["trend"] and is_corpus:
        return (f'SELECT report_date, SUM({mexpr}) AS {meas} FROM "{name}" '
                f"WHERE {where} GROUP BY report_date ORDER BY report_date")

    dim_col = _find_col(cols, intent["dim"]) if intent["dim"] else None
    if dim_col:
        limit = " LIMIT 1" if intent["top"] else ""
        return (f'SELECT "{dim_col}", SUM({mexpr}) AS {meas} FROM "{name}" '
                f'WHERE {where} GROUP BY "{dim_col}" ORDER BY {meas} DESC{limit}')

    return f'SELECT SUM({mexpr}) AS {meas} FROM "{name}" WHERE {where}'


def _format_rows(intent: dict, result: dict) -> str:
    rows = result.get("rows") or []
    disp = intent["measure"] or "value"
    flt = "".join(f" for {v}" for v in intent["filters"].values())
    if not rows:
        return f"No {disp} found{flt}."
    if len(rows) == 1 and len(rows[0]) == 1:  # scalar SUM
        return f"Total {disp}{flt} is {_fmt_num(rows[0][0])}."
    if intent["trend"]:
        seq = " → ".join(f"{r[0]}: {_fmt_num(r[1])}" for r in rows)
        return f"{disp.capitalize()}{flt} by week — {seq}."
    if intent["top"] and len(rows[0]) >= 2:
        return f"{rows[0][0]} has the highest {disp} ({_fmt_num(rows[0][1])})."
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
    # pick the table that has the measure (and the dimension too, if one was asked).
    chosen = None
    for t in tables:
        if _find_col(t["header"], meas):
            if not intent["dim"] or _find_col(t["header"], intent["dim"]):
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

    disp = meas
    if intent["dim"]:
        di = hdr.index(_find_col(hdr, intent["dim"]))
        pairs = [(r[di], num(r[mi])) for r in data if mi < len(r) and num(r[mi]) is not None]
        if not pairs:
            return None
        if intent["top"]:
            best = max(pairs, key=lambda p: p[1])
            return f"{best[0]} has the highest {disp} ({_fmt_num(best[1])})."
        pairs.sort(key=lambda p: p[1], reverse=True)
        body = ", ".join(f"{a} {_fmt_num(b)}" for a, b in pairs[:8])
        return f"{disp.capitalize()} by {intent['dim']}: {body}."
    vals = [num(r[mi]) for r in data if mi < len(r) and num(r[mi]) is not None]
    if not vals:
        return None
    return f"Total {disp} is {_fmt_num(sum(vals))}."


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
        is_corpus = "run_corpus_sql" in available

        # 1) a tool just returned -----------------------------------------------
        if fr is not None and fr.name != "transfer_to_agent":
            if fr.name in ("list_sql_schema", "list_corpus_schema"):
                schema = _normalize_schema(fr)
                sql = _build_sql(_parse_intent(user_text), schema, is_corpus)
                run_tool = "run_corpus_sql" if is_corpus else "run_sql"
                if sql:
                    return self._log(f"{run_tool}:domain", _call_part(run_tool, {"query": sql}))
                # couldn't express it -> let the base mock's generic SELECT run.
                return super()._decide(req)
            if fr.name in ("run_sql", "run_corpus_sql"):
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
        if "list_corpus_schema" in available:
            return self._log("list_corpus_schema", _call_part("list_corpus_schema", {}))
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
