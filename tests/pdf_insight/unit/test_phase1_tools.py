"""Phase 1 — deterministic PDF extraction. Pure functions, no LLM, no runner."""
from shared import pdf_extractor as pdf

PDF = "tests/pdf_insight/fixtures/risk_report.pdf"


def test_detects_tables():
    tables = pdf.extract_tables(PDF)
    assert len(tables) >= 1
    # global, stable indices across pages
    assert [t["index"] for t in tables] == list(range(len(tables)))


def test_headers_are_sanitized_and_unique():
    tables = pdf.extract_tables(PDF)
    for t in tables:
        assert len(t["header"]) == len(set(t["header"]))  # unique
        assert all(h for h in t["header"])                # no blanks (col_N fallback)


def test_select_filters_to_requested_indices():
    only = pdf.extract_tables(PDF, select=[0])
    assert [t["index"] for t in only] == [0]


def test_tables_as_text_is_model_friendly():
    text = pdf.tables_as_text(pdf.extract_tables(PDF, select=[0]))
    assert "Table 0" in text and " | " in text


def test_tables_as_text_handles_empty():
    assert "no tables" in pdf.tables_as_text([])
