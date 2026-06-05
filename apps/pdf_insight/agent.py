"""pdf_insight app: one coordinator, four PDF strategies, hybrid routing.

Read this before changing the topology — it mixes every ADK agent kind on purpose.

Modes (see config.py for the precedence chain that selects one):
  LLM_GETS_ALL_TABLES_AS_TEXT  -> extract every table, render to text, model answers
  LLM_GETS_SOME_TABLES_AS_TEXT -> same, but only selected table indices
  LLM_GIVES_SQL_FROM_TEXT      -> tables -> SQLite -> Text2SQL agent (NL->SQL->run)
  LLM_GETS_PDF_BYTES           -> native multimodal upload (Gemini only; later phase)

Agent kinds used here:
  * PdfCoordinator (custom BaseAgent) -- the hybrid router. When a mode is pinned
    it dispatches DETERMINISTICALLY (no routing LLM); only `auto` defers to a
    reasoning LlmAgent. This is the cleanest expression of "partly configurable,
    partly reasoning based".
  * TablesAnswerAgent (custom BaseAgent) -- does the deterministic extraction,
    publishes the table text to state, then delegates to an answering LlmAgent
    whose instruction pulls that text in via `{pdf_tables_text}` templating.
  * SqlModeAgent (custom BaseAgent) -- runs deterministic ingestion, then hands
    off to the Text2SQL LlmAgent.
  * router_agent / text2sql_agent / answerers -- LlmAgents (genuine reasoning).

Why custom BaseAgents instead of more LlmAgents? Mode selection-when-pinned,
table extraction, and SQLite ingestion are all deterministic. Deterministic work
belongs in code/tools, never in an LlmAgent. `root_agent` is what ADK discovers.
"""
from __future__ import annotations

import os
import re
import tempfile
from typing import AsyncGenerator, Optional

from shared import pdf
from shared.model import backend, get_model, is_gemini

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

from . import config
from .sql_tools import ingest_tables_to_sqlite, list_sql_schema, run_sql
from .tools import extract_tables, set_pdf_mode

_DEFAULT_PDF = os.path.join("data", "ii_statement.pdf")


# --- small helpers -----------------------------------------------------------
def _user_text(ctx: InvocationContext) -> str:
    content = ctx.user_content
    if content and content.parts:
        for p in content.parts:
            if p.text:
                return p.text
    return ""


def _resolve_pdf_path(text: str, state: dict) -> str:
    """Precedence: a .pdf path in the message > state['pdf_path'] > env > default."""
    m = re.search(r"(\S+\.pdf)\b", text or "", re.IGNORECASE)
    if m:
        return m.group(1)
    return state.get("pdf_path") or os.environ.get("PDF_PATH") or _DEFAULT_PDF


def _parse_table_indices(text: str) -> Optional[list[int]]:
    """Pull explicit table indices ('table 0', 'tables 0,2') out of the message."""
    m = re.search(r"tables?\s+([\d,\s]+)", text or "", re.IGNORECASE)
    if not m:
        return None
    nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
    return nums or None


def _text_event(author: str, text: str, state_delta: dict | None = None) -> Event:
    return Event(
        author=author,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        actions=EventActions(state_delta=state_delta or {}),
    )


# --- answering LlmAgents (one instance per parent: an agent has a single parent) ---
def _make_answerer(name: str) -> LlmAgent:
    return LlmAgent(
        name=name,
        model=get_model(),
        description="Answers a question using PDF tables already rendered to text.",
        # {pdf_tables_text?} is filled from session state by the preceding stage.
        instruction=(
            "You are given tables extracted from a PDF:\n\n{pdf_tables_text?}\n\n"
            "Answer the user's question using ONLY these tables. If the answer "
            "isn't present, say so."
        ),
    )


class TablesAnswerAgent(BaseAgent):
    """Deterministic extract -> publish to state -> delegate to an answering LLM.

    `select=None` extracts all tables (LLM_GETS_ALL_TABLES_AS_TEXT); a list of
    indices extracts just those (LLM_GETS_SOME_TABLES_AS_TEXT). For the 'some'
    case the indices can also be overridden per request (e.g. 'table 2').
    """

    select: Optional[list[int]] = None
    answerer: LlmAgent

    def __init__(self, name: str, answerer: LlmAgent, select=None):
        super().__init__(name=name, answerer=answerer, select=select,
                         sub_agents=[answerer])

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        path = ctx.session.state.get("pdf_path")
        select = self.select
        if select is not None:  # allow a per-request index override
            override = _parse_table_indices(_user_text(ctx))
            if override is not None:
                select = override
        try:
            tables = pdf.extract_tables(path, select=select)
        except Exception as e:  # noqa: BLE001 - report parse failure to the user
            yield _text_event(self.name, f"Could not read tables from {path}: {e}")
            return
        text = pdf.tables_as_text(tables)
        # Mutate in-process (so the answerer's {pdf_tables_text} templating sees it
        # this turn) AND emit a state_delta so it persists in the session snapshot.
        ctx.session.state["pdf_tables_text"] = text
        yield Event(author=self.name,
                    actions=EventActions(state_delta={"pdf_tables_text": text}))
        async for ev in self.answerer.run_async(ctx):
            yield ev


class SqlModeAgent(BaseAgent):
    """Deterministic SQLite ingestion -> delegate to the Text2SQL LlmAgent."""

    text2sql: LlmAgent

    def __init__(self, name: str, text2sql: LlmAgent):
        super().__init__(name=name, text2sql=text2sql, sub_agents=[text2sql])

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        path = ctx.session.state.get("pdf_path")
        # Keep the SQLite file out of the source tree — derive it under the OS temp
        # dir from the PDF name so re-runs reuse it but the repo stays clean.
        default_db = os.path.join(tempfile.gettempdir(), os.path.basename(path) + ".sqlite")
        db_path = ctx.session.state.get("db_path") or default_db
        if not ctx.session.state.get("db_path"):
            try:
                ingest_tables_to_sqlite(path, db_path)
            except Exception as e:  # noqa: BLE001
                yield _text_event(self.name, f"Could not ingest {path}: {e}")
                return
            ctx.session.state["db_path"] = db_path  # in-process: tools read it now
            yield Event(author=self.name,  # persist so ingestion is reused next turn
                        actions=EventActions(state_delta={"db_path": db_path}))
        async for ev in self.text2sql.run_async(ctx):
            yield ev


class NativeBytesAgent(BaseAgent):
    """LLM_GETS_PDF_BYTES placeholder. Gemini-only; not wired in the first pass."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        if not is_gemini():
            msg = ("LLM_GETS_PDF_BYTES needs the gemini backend (native PDF upload "
                   f"can't run on the {backend()} backend). Pick another mode or "
                   "set LLM_BACKEND=gemini.")
        else:
            msg = "LLM_GETS_PDF_BYTES (native upload) is planned for a later phase."
        yield _text_event(self.name, msg)


# --- the specialists ---------------------------------------------------------
router_agent = LlmAgent(
    name="pdf_router",
    model=get_model(),
    description="Auto mode: reasons about the question and pulls tables as needed.",
    instruction=(
        "Answer questions about the active PDF. Call extract_tables to read its "
        "tables, then answer from them. You may pin a strategy with set_pdf_mode."
    ),
    tools=[extract_tables, set_pdf_mode],
)

text2sql_agent = LlmAgent(
    name="text2sql_agent",
    model=get_model(),
    description="Writes ONE read-only SQLite SELECT to answer questions over tables.",
    instruction=(
        "First call list_sql_schema to see the tables and columns. Then write a "
        "single SQLite SELECT, call run_sql with it, and answer from the rows. "
        "Never write or modify data."
    ),
    tools=[list_sql_schema, run_sql],
)

_tables_all = TablesAnswerAgent("tables_all_mode", _make_answerer("answer_all"))
_tables_some = TablesAnswerAgent("tables_some_mode", _make_answerer("answer_some"),
                                 select=[0])
_sql_mode = SqlModeAgent("sql_mode", text2sql_agent)
_native = NativeBytesAgent(name="native_bytes_mode")


class PdfCoordinator(BaseAgent):
    """Hybrid config/reasoning router (see module docstring).

    Resolves the mode from the precedence chain, records it on the session and a
    one-line UI banner, then dispatches: a pinned mode goes straight to its
    specialist (no routing LLM); `auto` defers to the reasoning router.
    """

    router: LlmAgent
    dispatch: dict

    def __init__(self, name, router, dispatch):
        super().__init__(name=name, router=router, dispatch=dispatch,
                         sub_agents=[router, *dispatch.values()])

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        text = _user_text(ctx)
        state = ctx.session.state
        mode = config.resolve_mode(
            config.parse_request_override(text), state, os.environ
        )
        path = _resolve_pdf_path(text, state)

        # Make path/mode visible immediately (children read state in-process) and
        # durable (state_delta on the banner event the UI shows).
        state["pdf_path"] = path
        state["active_pdf_mode"] = mode
        yield _text_event(
            self.name, f"▸ mode: {mode}  ·  pdf: {path}",
            state_delta={"active_pdf_mode": mode, "pdf_path": path},
        )

        target = self.router if mode == config.AUTO else self.dispatch[mode]
        async for ev in target.run_async(ctx):
            yield ev


root_agent = PdfCoordinator(
    name="pdf_insight",
    router=router_agent,
    dispatch={
        config.ALL_TABLES_AS_TEXT: _tables_all,
        config.SOME_TABLES_AS_TEXT: _tables_some,
        config.SQL_FROM_TEXT: _sql_mode,
        config.PDF_BYTES: _native,
    },
)
