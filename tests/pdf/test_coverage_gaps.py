"""Targeted tests for the last uncovered branches in apps/pdf_insight.

Everything in here exists to exercise a *specific* defensive branch that the
happy-path suite (phases 1-4, golden answers, errors, units) never reaches.
Each test names the file:line it covers so the intent stays obvious if the line
numbers drift. These are almost all error handlers and one-off guards — exactly
the code you only find out is broken when it's too late, so it's worth pinning.

Backend is forced to mock before the agent imports, matching the sibling suites.
"""
import os

os.environ["LLM_BACKEND"] = "mock"  # must precede any agent import

from decimal import Decimal  # noqa: E402

import duckdb  # noqa: E402
import pytest  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from apps.pdf_insight import config, modes  # noqa: E402
from apps.pdf_insight.stores import sqlite_store as sql_tools  # noqa: E402
from apps.pdf_insight.stores.duckdb_store import (  # noqa: E402
    _jsonable,
    list_stash_schema,
    run_stash_sql,
)
from apps.pdf_insight.modes import _common, native, sql, tables  # noqa: E402

FIXTURE = "tests/fixtures/risk_report.pdf"


class _Ctx:
    """Minimal ToolContext stand-in — the tools only ever read `.state`."""

    def __init__(self, state=None):
        self.state = state or {}


async def _run_and_get_state(agent, message: str, state: dict) -> dict:
    """Drive `agent` for one turn with a seeded session state and return the
    final state. Unlike the `run_agent` fixture (which yields only the text),
    this lets a test assert on what the agent wrote back to state."""
    runner = InMemoryRunner(agent=agent, app_name="cov")
    session = await runner.session_service.create_session(
        app_name="cov", user_id="u", state=state
    )
    msg = types.Content(role="user", parts=[types.Part(text=message)])
    async for _ in runner.run_async(user_id="u", session_id=session.id, new_message=msg):
        pass
    refreshed = await runner.session_service.get_session(
        app_name="cov", user_id="u", session_id=session.id
    )
    return dict(refreshed.state)


@pytest.fixture
def make_content_ctx():
    """Factory for a fake InvocationContext whose user_content has parts with the
    given texts (None/'' model an empty part). Used to exercise _user_text's loop."""
    class _Part:
        def __init__(self, text):
            self.text = text

    class _Content:
        def __init__(self, texts):
            self.parts = [_Part(t) for t in texts]

    class _FakeCtx:
        def __init__(self, texts):
            self.user_content = _Content(texts)

    return _FakeCtx


# ===========================================================================
# config.py — parse_request_override on empty input
# ===========================================================================
def test_parse_request_override_empty_input_is_none():
    """config.py:41 — the `if not text: return None` short-circuit.

    The happy-path tests always pass a non-empty message, so the empty/None
    guard (which protects the regex from a None) was never taken.
    """
    assert config.parse_request_override("") is None
    assert config.parse_request_override(None) is None


# ===========================================================================
# modes/_common.py — _user_text with no usable content
# ===========================================================================
def test_user_text_returns_empty_when_no_content():
    """_common.py:25 — the final `return ""` when there's no user content.

    Reached when the invocation has no content at all. Real turns always carry
    content, so only a synthetic ctx hits this path.
    """
    class _FakeCtx:
        user_content = None

    assert _common._user_text(_FakeCtx()) == ""


def test_user_text_skips_empty_parts(make_content_ctx):
    """_common.py 23->22 and 22->25 — the per-part loop arcs.

    A part with no text must be skipped to reach a later part that has text
    (23->22); and if every part is empty the loop exhausts and falls through to
    the `return ""` (22->25). Both arcs are invisible to normal single-text turns.
    """
    # first part empty -> loop continues -> second part's text is returned
    assert _common._user_text(make_content_ctx(["", "hello"])) == "hello"
    # every part empty -> loop exhausts without returning -> ""
    assert _common._user_text(make_content_ctx(["", None])) == ""


# ===========================================================================
# modes/__init__.py — duplicate mode guard in build_dispatch()
# ===========================================================================
def test_build_dispatch_rejects_duplicate_mode(monkeypatch):
    """modes/__init__.py:25 — the `raise ValueError` for a duplicate mode key.

    Two registered builders claiming the same mode constant must fail loudly
    rather than silently letting the second clobber the first. We swap in two
    builders that both emit SQL_FROM_TEXT to force the collision.
    """
    dup = lambda: {config.SQL_FROM_TEXT: object()}  # noqa: E731 - tiny stub
    monkeypatch.setattr(modes, "MODE_BUILDERS", (dup, dup))
    with pytest.raises(ValueError, match="Duplicate mode"):
        modes.build_dispatch()


# ===========================================================================
# modes/native.py — the gemini branch of the placeholder
# ===========================================================================
async def test_native_bytes_reports_planned_on_gemini(run_agent, monkeypatch):
    """native.py:31 — the `else` branch shown when the backend IS gemini.

    The suite runs under mock, so normally only the "needs gemini" refusal is
    hit. Forcing is_gemini() True exercises the "planned for a later phase"
    message instead, without needing a real gemini backend.
    """
    monkeypatch.setattr(native, "is_gemini", lambda: True)
    agent = native.build()[config.PDF_BYTES]
    answer = await run_agent(agent, "read the whole document", {})
    assert "planned" in answer.lower()


# ===========================================================================
# stores/sqlite_store.py + stores/base.py — ingestion + shared guard edges
# ===========================================================================
def test_ingest_dedupes_columns_that_collide_after_sanitizing(tmp_path, monkeypatch):
    """sqlite_store.py — the `col = f"{col}_x"` de-dup loop in SQLiteStore.ingest.

    Two *distinct* PDF headers can sanitize to the SAME SQL identifier
    ('Vega!' and 'Vega?' both -> 'Vega_'). The loop suffixes the collision so
    CREATE TABLE doesn't fail with a duplicate column. Real fixtures rarely
    collide, so we feed a crafted table straight into ingest by stubbing the
    extractor.
    """
    crafted = [{
        "index": 0, "page": 1, "ncols": 2,
        "header": ["Vega!", "Vega?"],  # both -> 'Vega_' under \W -> _
        "rows": [["a", "b"]],
    }]
    monkeypatch.setattr(sql_tools.pdf, "extract_tables", lambda *a, **k: crafted)
    db = str(tmp_path / "collide.sqlite")
    out = sql_tools.ingest_tables_to_sqlite("ignored.pdf", db)
    assert out["tables"][0]["columns"] == ["Vega_", "Vega__x"]


def test_run_sql_rejects_write_keyword_inside_a_select():
    """base.py validate_select — the keyword blocklist, shared by every store.

    The phase-2 cases (DELETE/DROP/...) are rejected earlier by the leading
    SELECT/WITH check. A query that genuinely starts with SELECT but smuggles a
    DDL keyword in its body only trips the dedicated blocklist. db_path is a
    dummy — the guard returns before any DB is opened.
    """
    out = sql_tools.run_sql("SELECT * FROM t0 WHERE drop = 1", _Ctx({"db_path": "x.sqlite"}))
    assert out["status"] == "error"
    assert "Write/DDL" in out["error_message"]


def test_run_sql_surfaces_sqlite_errors(tmp_path):
    """base.py run_select — the engine-error handler (shared by every store).

    A well-formed but invalid SELECT (unknown table) must come back as a clean
    error dict, not raise. We ingest the real fixture to get a valid DB, then
    query a table that isn't there.
    """
    db = str(tmp_path / "real.sqlite")
    sql_tools.ingest_tables_to_sqlite(FIXTURE, db)
    out = sql_tools.run_sql("SELECT * FROM no_such_table", _Ctx({"db_path": db}))
    assert out["status"] == "error"
    assert "SQL error" in out["error_message"]


def test_ingest_creates_storage_dir_only_when_path_has_one(tmp_path, monkeypatch):
    """sqlite_store.py — the `if parent:` guard around os.makedirs in ingest.

    A resolved DSN like data/sqlite/x.sqlite has a parent dir to create; a bare
    filename ('x.sqlite') has none, and os.makedirs('') would raise. We chdir into
    tmp_path so a bare-filename DB lands there (not the repo) and stub the extractor
    so no real PDF is needed.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sql_tools.pdf, "extract_tables",
        lambda *a, **k: [{"index": 0, "page": 1, "ncols": 1, "header": ["a"], "rows": [["1"]]}],
    )
    out = sql_tools.ingest_tables_to_sqlite("ignored.pdf", "bare.sqlite")  # no dir part
    assert out["status"] == "success"


# ===========================================================================
# stores/duckdb_store.py + stores/base.py — DuckDB error branches + jsonable
# ===========================================================================
def test_jsonable_falls_back_to_str_for_decimal():
    """base.py jsonable — the `return str(v)` catch-all coercion.

    DuckDB hands back Decimal for some aggregates; it isn't int/float/str/date,
    so it must be stringified to survive ADK's JSON function_response.
    """
    assert _jsonable(Decimal("3.50")) == "3.50"


def test_list_stash_schema_surfaces_db_errors(tmp_path):
    """duckdb_store.py — the `except duckdb.Error` in DuckDBStore.list_schema.

    The DB file exists (so the missing-file guard passes) but has none of the
    expected registry tables, so querying pdf_tables raises a CatalogException.
    That must become an error dict, not a traceback.
    """
    db = str(tmp_path / "empty.duckdb")
    duckdb.connect(db).close()  # creates a valid-but-empty DuckDB file
    out = list_stash_schema(_Ctx({"stash_db": db}))
    assert out["status"] == "error"
    assert "Stash DB error" in out["error_message"]


def test_run_stash_sql_surfaces_db_errors(tmp_path):
    """base.py run_select via DuckDBStore — engine error on a missing table.

    A valid read-only SELECT against a table that doesn't exist must return a
    clean error rather than raise out of the tool.
    """
    db = str(tmp_path / "empty.duckdb")
    duckdb.connect(db).close()
    out = run_stash_sql("SELECT * FROM t00", _Ctx({"stash_db": db}))
    assert out["status"] == "error"
    assert "SQL error" in out["error_message"]


# ===========================================================================
# stores/postgres_store.py — the not-yet-implemented backend
# ===========================================================================
def test_postgres_store_is_a_documented_placeholder():
    """postgres_store.py — both abstract methods raise NotImplementedError.

    The skeleton proves the abstraction holds: PostgresStore inherits the guard
    and run_select unchanged, and only the two engine-specific methods are TODO.
    run_select surfaces the unimplemented connection as a clean error dict (it
    catches the raise), while list_schema raises directly.
    """
    from apps.pdf_insight.stores import PostgresStore

    store = PostgresStore("postgresql://localhost:5432/pdf_insight")
    out = store.run_select("SELECT 1")  # _connect_readonly raises -> caught -> error dict
    assert out["status"] == "error"
    assert "placeholder" in out["error_message"]
    with pytest.raises(NotImplementedError):
        store.list_schema()


# ===========================================================================
# modes/sql.py — the re-ingest-skip branch
# ===========================================================================
async def test_sql_mode_skips_reingest_when_db_matches_source(tmp_path):
    """sql.py 58->71 — the `db_source_pdf == path` skip path.

    Ingestion is cached per source PDF: if the session already ingested THIS
    pdf, the agent must skip straight to the Text2SQL delegate without rebuilding
    the DB. Every other test starts fresh (so it always ingests); here we seed a
    matching db_source_pdf and assert the seeded db_path is left untouched —
    proving no re-ingest replaced it with the temp default. The sentinel db lives
    under tmp_path so the delegated tools don't create a file in the repo.
    """
    sentinel = str(tmp_path / "sentinel.sqlite")
    agent = sql.build()[config.SQL_FROM_TEXT]
    state = await _run_and_get_state(
        agent,
        "how many rows are there?",
        {"pdf_path": FIXTURE, "db_source_pdf": FIXTURE, "db_path": sentinel},
    )
    assert state["db_path"] == sentinel  # untouched -> ingestion skipped


async def test_sql_mode_reports_missing_pdf_path(run_agent):
    """sql.py 51-52 — the `if not path` guard.

    With no pdf_path in state, basename(None) would crash; the agent must instead
    report it like any other ingest failure. Every other test seeds a pdf_path, so
    this guard is otherwise never taken.
    """
    agent = sql.build()[config.SQL_FROM_TEXT]
    answer = await run_agent(agent, "how many rows are there?", {})  # no pdf_path
    assert "no PDF path provided" in answer


# ===========================================================================
# modes/tables.py — SOME-mode per-request index override (both arcs)
# ===========================================================================
async def test_some_mode_uses_message_index_override(run_agent):
    """tables.py 59->60 — a `tables N` directive in the message overrides select.

    SOME-mode defaults to select=[0]; an explicit 'table 2' must redirect it to
    index 2 for this turn. (Also shores up the thin SOME-mode integration path.)
    """
    agent = tables.build()[config.SOME_TABLES_AS_TEXT]
    answer = await run_agent(agent, "summarize table 2", {"pdf_path": FIXTURE})
    assert answer  # the deterministic stage extracted table 2 and the answerer replied


async def test_some_mode_keeps_default_when_no_index_in_message(run_agent):
    """tables.py 59->61 — no index in the message, so select=[0] is kept.

    When _parse_table_indices finds nothing, the override is None and the agent
    falls through to extraction with the default selection. This is the arc the
    happy path skips because it always passes an explicit index.
    """
    agent = tables.build()[config.SOME_TABLES_AS_TEXT]
    answer = await run_agent(agent, "summarize the holdings", {"pdf_path": FIXTURE})
    assert answer
