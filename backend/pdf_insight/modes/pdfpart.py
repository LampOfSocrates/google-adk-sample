"""Tables-as-text modes: LLM_GETS_ALL_TABLES_AS_TEXT / LLM_GETS_SOME_TABLES_AS_TEXT.

Extract -> publish to state -> delegate to an answering LlmAgent that pulls the
text back via `{pdf_tables_text}` templating. Both modes share one BaseAgent;
`select` distinguishes them.
"""
from __future__ import annotations

from typing import AsyncGenerator, Optional

from backend.shared import pdf_extractor as pdf
from backend.shared.model import get_model

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from .. import config
from ._common import _parse_table_indices, _text_event, _user_text


def _make_answerer(name: str) -> LlmAgent:
    """One answerer instance per parent (an ADK agent has a single parent)."""
    return LlmAgent(
        name=name,
        model=get_model(),
        description="Answers a question using PDF tables already rendered to text.",
        # {pdf_tables_text?} is filled from state by the preceding stage.
        instruction=(
            "You are given tables extracted from a PDF document. The document may be "
            "any kind — a report, statement, invoice, form, dataset, or research table — "
            "so don't assume a domain; read the column headers to learn what each table "
            "holds:\n\n{pdf_tables_text?}\n\n"
            "Answer the user's question using ONLY these tables. When you aggregate over "
            "rows (sum, average, count, max), skip any subtotal/'Total' row so you don't "
            "double count, and keep the units shown in the headers. If the answer isn't "
            "present in the tables, say so plainly rather than guessing."
        ),
    )


class PdfPartTextAgent(BaseAgent):
    """Extract -> publish to state -> delegate to an answering LLM.

    `select=None` extracts all tables (the 'all' mode); a list of indices extracts
    just those (the 'some' mode), and those can be overridden per request ('table 2').
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
        if select is not None:  # per-request index override
            override = _parse_table_indices(_user_text(ctx))
            if override is not None:
                select = override
        try:
            tables = pdf.extract_tables(path, select=select)
        except Exception as e:  # noqa: BLE001 - report parse failure to the user
            yield _text_event(self.name, f"Could not read tables from {path}: {e}")
            return
        text = pdf.tables_as_text(tables)
        # Mutate in-process so the templating sees it this turn; state_delta persists it.
        ctx.session.state["pdf_tables_text"] = text
        yield Event(author=self.name,
                    actions=EventActions(state_delta={"pdf_tables_text": text}))
        async for ev in self.answerer.run_async(ctx):
            yield ev


def build() -> dict:
    """Return {mode_constant: agent} for the two tables-as-text modes."""
    return {
        config.ALL_TABLES_AS_TEXT: PdfPartTextAgent(
            "pdfpart_all", _make_answerer("pdfpart_all_agent")),
        config.SOME_TABLES_AS_TEXT: PdfPartTextAgent(
            "pdfpart_some", _make_answerer("pdfpart_some_agent"), select=[0]),
    }
