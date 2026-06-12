"""Backend-agnostic SQL store abstraction.

A `SqlStore` hides WHICH engine holds the extracted PDF tables behind the two
operations the agents actually need: list the schema, and run ONE read-only
query. The security-critical parts live HERE so every backend enforces them
identically:

  * the read-only trust boundary — single statement, leading SELECT/WITH, no
    write/DDL keywords (`validate_select`);
  * JSON-safe coercion of engine scalars (`jsonable`);
  * the `run_select` execution path itself.

Only two things genuinely differ per engine, so only two things are abstract:
  * `_connect_readonly()` — how you open a read-only handle (SQLite PRAGMA,
    DuckDB read_only flag, Postgres read-only transaction);
  * `list_schema()` — how you describe what's queryable.

That's the whole point: adding `PostgresStore` means implementing those two, not
re-deriving the guard or the result shape. See `postgres_store.py`.
"""
from __future__ import annotations

import datetime as dt
import re
from abc import ABC, abstractmethod
from typing import Any

_MAX_ROWS = 200

# One read-only guard for every backend. `replace` is deliberately NOT blocked:
# the leading-SELECT + single-statement rules already reject the REPLACE INTO
# write statement, while REPLACE() is a useful read-only scalar (strip commas
# before CAST). A prompt can be talked into writing DELETE; this guard cannot.
_FORBIDDEN = re.compile(
    r"(?i)\b(insert|update|delete|drop|alter|create|attach|copy|pragma|install|load|export)\b"
)
_LEADING_SELECT = re.compile(r"(?is)^\s*(select|with)\b")


def validate_select(query: str) -> str | None:
    """Return an error message if `query` is not a single read-only SELECT/WITH,
    else None. This is the trust boundary the LLM-written SQL must clear."""
    q = query.strip().rstrip(";").strip()
    if ";" in q:
        return "Only a single statement is allowed."
    if not _LEADING_SELECT.match(q):
        return "Only SELECT / WITH queries are allowed."
    if _FORBIDDEN.search(q):
        return "Write/DDL keywords are not allowed."
    return None


def jsonable(v: Any):
    """Coerce an engine scalar to a JSON-safe value so it survives ADK's
    function_response serialization. DuckDB hands back date/datetime/Decimal;
    everything unrecognised is stringified."""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, (int, float, str)) or v is None:
        return v
    return str(v)


class SqlStore(ABC):
    """A queryable store of extracted PDF tables, on some SQL engine.

    Subclasses implement the connection and schema; the validated read-only query
    path (`run_select`) and the result shape are shared so backends are
    interchangeable from the agent's point of view.
    """

    max_rows = _MAX_ROWS

    # Store-specific SQL-dialect guidance appended to a mode agent's prompt. Empty
    # for engines whose queries are ANSI-clean; the agent factory only appends it
    # when non-empty. This is the third (and last) thing that differs per engine —
    # alongside _connect_readonly and list_schema.
    dialect_hint: str = ""

    @abstractmethod
    def _connect_readonly(self):
        """Open a READ-ONLY connection/handle to this store's engine."""

    @abstractmethod
    def ingest_pdf(self, pdf_path: str, report_date: str | None = None,
                   strategy: str = "lines", title_for=None) -> dict:
        """Ingest one PDF's extracted tables into this store. One name, one
        signature for every backend (this is the contract the divergence note
        asked for). WHAT it does is store-specific:

          * per-document stores (SQLite) REPLACE — they hold the latest PDF only,
            so `report_date`/`title_for` don't apply and are ignored;
          * corpus stores (DuckDB/Postgres) APPEND, idempotent per `report_date`
            (defaulted from the filename, else today), with `title_for(index)`
            supplying table titles.

        Returns a `{status, ...}` summary. `strategy` is the pdfplumber table
        strategy, honored by every backend."""

    @abstractmethod
    def list_schema(self) -> dict:
        """Describe what's queryable. Shape is engine-specific but always carries
        a `status` key and, on success, the columns an agent can SELECT."""

    def available(self) -> str | None:
        """Return an error message if the store can't be queried yet (e.g. its
        file is missing), else None. Default: always available."""
        return None

    def run_select(self, query: str) -> dict:
        """Validate and execute ONE read-only query. Uniform result across engines:
        {status, columns, rows, row_count} on success, {status, error_message} else."""
        unavailable = self.available()
        if unavailable:
            return {"status": "error", "error_message": unavailable}
        err = validate_select(query)
        if err:
            return {"status": "error", "error_message": err}
        q = query.strip().rstrip(";").strip()
        try:  # connecting can fail on its own (missing/locked db, unimplemented engine)
            con = self._connect_readonly()
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error_message": f"SQL error: {e}"}
        try:  # con is open here, so finally always closes it (no None guard needed)
            cur = con.execute(q)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [[jsonable(c) for c in r] for r in cur.fetchmany(self.max_rows)]
        except Exception as e:  # noqa: BLE001 - any engine error -> clean dict
            return {"status": "error", "error_message": f"SQL error: {e}"}
        finally:
            con.close()
        return {"status": "success", "columns": columns, "rows": rows,
                "row_count": len(rows)}
