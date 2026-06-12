"""Backend-agnostic SQL store abstraction.

`SqlStore` hides which engine holds the PDF tables behind two ops: list schema,
run one read-only query. The security-critical bits live here so every backend
enforces them the same — the read-only guard (`validate_select`), scalar
coercion (`jsonable`), and `run_select`. Only `_connect_readonly` and
`list_schema` differ per engine, so a new backend implements just those two.
"""
from __future__ import annotations

import datetime as dt
import re
from abc import ABC, abstractmethod
from typing import Any

_MAX_ROWS = 200

# One read-only guard for every backend. `replace` isn't blocked: the
# leading-SELECT + single-statement rules already reject REPLACE INTO, and
# REPLACE() is a useful read-only scalar (strip commas before CAST).
_FORBIDDEN = re.compile(
    r"(?i)\b(insert|update|delete|drop|alter|create|attach|copy|pragma|install|load|export)\b"
)
_LEADING_SELECT = re.compile(r"(?is)^\s*(select|with)\b")


def validate_select(query: str) -> str | None:
    """Error message if `query` isn't a single read-only SELECT/WITH, else None.

    The trust boundary LLM-written SQL must clear: blocks writes/DDL and
    multi-statements (SQL injection).
    """
    q = query.strip().rstrip(";").strip()
    if ";" in q:
        return "Only a single statement is allowed."
    if not _LEADING_SELECT.match(q):
        return "Only SELECT / WITH queries are allowed."
    if _FORBIDDEN.search(q):
        return "Write/DDL keywords are not allowed."
    return None


def jsonable(v: Any):
    """Coerce an engine scalar to JSON-safe so it survives ADK serialization.

    DuckDB hands back date/datetime/Decimal; anything unrecognised is stringified.
    """
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, (int, float, str)) or v is None:
        return v
    return str(v)


class SqlStore(ABC):
    """A queryable store of extracted PDF tables on some SQL engine.

    Subclasses implement connect + schema; `run_select` and the result shape are
    shared, so backends are interchangeable to the agent.
    """

    max_rows = _MAX_ROWS

    # SQL-dialect hint appended to the mode agent's prompt (only when non-empty).
    # Empty for ANSI-clean engines. The third per-engine difference, alongside
    # _connect_readonly and list_schema.
    dialect_hint: str = ""

    @abstractmethod
    def _connect_readonly(self):
        """Open a read-only handle to this store's engine."""

    @abstractmethod
    def ingest_pdf(self, pdf_path: str, report_date: str | None = None,
                   strategy: str = "lines", title_for=None) -> dict:
        """Ingest one PDF's tables. Same signature everywhere; behavior differs:

          * per-document (SQLite) REPLACEs — latest PDF only, so `report_date`/
            `title_for` are ignored;
          * corpus (DuckDB/Postgres) APPENDs, idempotent per `report_date`
            (from filename, else today), with `title_for(index)` for titles.

        Returns a `{status, ...}` summary. `strategy` is the pdfplumber table
        strategy.
        """

    @abstractmethod
    def list_schema(self) -> dict:
        """Describe what's queryable. Engine-specific shape, but always a `status`
        key and, on success, the SELECTable columns."""

    def available(self) -> str | None:
        """Error message if the store can't be queried yet (e.g. missing file),
        else None. Default: always available."""
        return None

    def run_select(self, query: str) -> dict:
        """Validate and run one read-only query. Uniform result across engines:
        {status, columns, rows, row_count} on success, {status, error_message} else."""
        unavailable = self.available()
        if unavailable:
            return {"status": "error", "error_message": unavailable}
        err = validate_select(query)
        if err:
            return {"status": "error", "error_message": err}
        q = query.strip().rstrip(";").strip()
        try:  # connecting can fail (missing/locked db, unimplemented engine)
            con = self._connect_readonly()
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error_message": f"SQL error: {e}"}
        try:  # con is open, so finally always closes it
            cur = con.execute(q)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [[jsonable(c) for c in r] for r in cur.fetchmany(self.max_rows)]
        except Exception as e:  # noqa: BLE001 - any engine error -> clean dict
            return {"status": "error", "error_message": f"SQL error: {e}"}
        finally:
            con.close()
        return {"status": "success", "columns": columns, "rows": rows,
                "row_count": len(rows)}
