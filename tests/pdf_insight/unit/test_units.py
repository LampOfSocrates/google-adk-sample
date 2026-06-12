"""Gap 7 — leftover pure-ish branches: index parsing, pdf-path precedence,
single/multi-index select. Fast units, no agents."""
import pytest

from backend.pdf_insight.modes._common import (
    _DEFAULT_PDF,
    _parse_table_indices,
    _resolve_pdf_path,
)
from backend.shared import pdf_extractor as pdf

FIXTURE = "tests/pdf_insight/fixtures/risk_report.pdf"


@pytest.mark.parametrize("text, expected", [
    ("table 0", [0]),
    ("tables 0,2", [0, 2]),
    ("show me tables 0, 2, 5", [0, 2, 5]),
    ("no indices here", None),
    ("the whole document", None),
])
def test_parse_table_indices(text, expected):
    assert _parse_table_indices(text) == expected


def test_resolve_pdf_path_prefers_message_path():
    assert _resolve_pdf_path("look at report.pdf please", {"pdf_path": "state.pdf"}) == "report.pdf"


def test_resolve_pdf_path_falls_back_to_state(monkeypatch):
    monkeypatch.delenv("PDF_PATH", raising=False)
    assert _resolve_pdf_path("no path in here", {"pdf_path": "state.pdf"}) == "state.pdf"


def test_resolve_pdf_path_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("PDF_PATH", "env.pdf")
    assert _resolve_pdf_path("no path in here", {}) == "env.pdf"


def test_resolve_pdf_path_default(monkeypatch):
    monkeypatch.delenv("PDF_PATH", raising=False)
    assert _resolve_pdf_path("no path in here", {}) == _DEFAULT_PDF


def test_single_index_select():
    tabs = pdf.extract_tables(FIXTURE, select=[5])
    assert [t["index"] for t in tabs] == [5]


def test_multi_index_select():
    tabs = pdf.extract_tables(FIXTURE, select=[0, 2])
    assert {t["index"] for t in tabs} == {0, 2}
