"""Gap 3 — error branches: a malformed PDF must fail gracefully, not traceback.

Fires the `except` paths in modes/pdfpart.py and modes/text2sql.py and the extract_tables
tool's error dict, using a throwaway broken PDF (no committed fixture needed).
"""
import os

import pytest

from apps.pdf_insight import config
from apps.pdf_insight.modes import text2sql, pdfpart
from apps.pdf_insight.tools import extract_tables

os.environ["LLM_BACKEND"] = "mock"  # mode agents bind their model at build()


class _Ctx:
    def __init__(self, state=None):
        self.state = state or {}


@pytest.fixture
def broken_pdf(tmp_path):
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"not a pdf at all -- just garbage bytes\n")
    return str(p)


def test_extract_tables_tool_reports_unreadable_pdf(broken_pdf):
    out = extract_tables(_Ctx({"pdf_path": broken_pdf}))
    assert out["status"] == "error"
    assert "Failed to read PDF" in out["error_message"]


def test_extract_tables_tool_missing_path_is_error():
    out = extract_tables(_Ctx())
    assert out["status"] == "error"
    assert "pdf_path" in out["error_message"]


async def test_tables_mode_reports_unreadable(run_agent, broken_pdf):
    agent = pdfpart.build()[config.ALL_TABLES_AS_TEXT]
    answer = await run_agent(agent, "summarize the holdings", {"pdf_path": broken_pdf})
    assert "Could not read tables" in answer


async def test_sql_mode_reports_failed_ingest(run_agent, broken_pdf):
    agent = text2sql.build()[config.SQL_FROM_TEXT]
    answer = await run_agent(agent, "how many rows are there?", {"pdf_path": broken_pdf})
    assert "Could not ingest" in answer
