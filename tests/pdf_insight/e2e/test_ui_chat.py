"""Browser e2e: drive the actual Streamlit chat UI against a real Bedrock model.

This is the highest-fidelity pdf_insight test there is — it clicks through the
same surface a user does: pick the Bedrock backend, upload risk_report.pdf, pin a
query mode, type a question, and read the streamed answer back out of the DOM.

What it proves, per the two question shapes the user asked for:
  * point-in-time aggregation — over the single uploaded document, the four
    single-PDF modes (all/some tables-as-text, SQL-over-this-PDF, native PDF bytes)
    each return the golden total vega / max-vega region;
  * temporal, cross-document — the corpus mode queries the pre-seeded multi-week
    DuckDB and names several distinct report dates, so it genuinely grouped across
    documents, not one.

We pin ONE mode at a time (the user's ask) via the sidebar dropdown, so each case
exercises exactly one strategy end to end. Assertions are tolerant (comma/format
insensitive number match; substring date match) so a model's phrasing can't flake
them. The module skips without AWS creds (see conftest `_require_bedrock`).

    pytest -m e2e tests/pdf_insight/e2e/test_ui_chat.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_PDF = str(_FIXTURES / "risk_report.pdf")

GOLD = json.load(open(_FIXTURES / "risk_report.golden.json", encoding="utf-8"))["facts"]
VEGA = GOLD["totals"]["vega"]               # 6384 — golden total vega
REGION_MAX_VEGA = GOLD["region_max_vega"]   # "Americas"

# How long one Bedrock turn (model + any tool round-trips + streaming) may take.
ANSWER_TIMEOUT_MS = 180_000


# --------------------------------------------------------------- answer checks ---
def _has_number(text: str, value: int) -> bool:
    """Comma/format-insensitive: is `value` present in the model's prose answer?"""
    return str(value) in "".join(ch for ch in text if ch.isdigit())


# ----------------------------------------------------------- Streamlit driver ---
class ChatUI:
    """Thin Playwright wrapper over the Streamlit widgets this app renders."""

    def __init__(self, page: Page, url: str):
        self.page = page
        self.last_tokens = 0
        page.set_default_timeout(30_000)
        page.goto(url)
        # App is ready once the chat box has rendered.
        page.get_by_test_id("stChatInput").wait_for()

    def _select(self, label: str, option: str) -> None:
        """Pick `option` in the selectbox whose label contains `label`."""
        box = (self.page.locator('div[data-testid="stSelectbox"]')
               .filter(has_text=label).first)
        box.locator('div[data-baseweb="select"]').click()
        (self.page.get_by_role("option", name=re.compile(re.escape(option), re.I))
         .first.click())

    def choose_app(self, app: str) -> "ChatUI":
        self._select("App", app)
        return self

    def choose_backend(self, backend: str) -> "ChatUI":
        self._select("Backend", backend)
        return self

    def choose_mode(self, mode_label: str) -> "ChatUI":
        self._select("Query mode", mode_label)
        return self

    def upload(self, pdf_path: str) -> "ChatUI":
        self.page.locator(
            '[data-testid="stFileUploader"] input[type="file"]'
        ).set_input_files(pdf_path)
        # Server ingest is synchronous; wait for the per-file success line so the
        # active PDF + corpus are ready before we ask anything.
        self.page.get_by_text(re.compile(r"sqlite\s+success", re.I)).first.wait_for(
            timeout=90_000)
        return self

    def ask(self, question: str) -> str:
        """Send one message; return the assistant turn's full text once it finishes."""
        box = self.page.get_by_test_id("stChatInput").locator("textarea")
        box.click()
        box.fill(question)
        box.press("Enter")
        answer = self.page.locator('[data-testid="stChatMessage"]').last
        # The per-turn meta caption ("… tokens") is the last thing drawn, so it
        # marks the turn as finished without racing the streamed text. It also
        # carries the turn's total token usage (summed across every model call in
        # the turn — see adk_ui_stream.usage_totals), so we read it back out here.
        expect(answer).to_contain_text(re.compile(r"token", re.I),
                                       timeout=ANSWER_TIMEOUT_MS)
        text = answer.inner_text()
        m = re.search(r"([\d,]+)\s*tokens", text)
        self.last_tokens = int(m.group(1).replace(",", "")) if m else 0
        return text


@pytest.fixture
def chat(page: Page, streamlit_ui) -> ChatUI:
    """A UI already on pdf_insight + Bedrock with risk_report.pdf uploaded."""
    ui = ChatUI(page, streamlit_ui["url"])
    ui.choose_app("pdf_insight").choose_backend("bedrock").upload(FIXTURE_PDF)
    return ui


# ------------------------------------------------- point-in-time aggregation ---
# One pinned single-PDF mode per row: (dropdown label, question, assertion).
POINT_IN_TIME = [
    pytest.param("all tables",
                 "What is the total vega across the whole portfolio?",
                 lambda a: _has_number(a, VEGA), id="all-tables-text"),
    pytest.param("some tables",
                 "What is the total vega?",
                 lambda a: _has_number(a, VEGA), id="some-tables-text"),
    pytest.param("SQL over THIS pdf",
                 "Which region has the most vega?",
                 lambda a: REGION_MAX_VEGA.lower() in a.lower(), id="sql-this-pdf"),
    pytest.param("raw pdf bytes",
                 "What is the total vega across the whole portfolio?",
                 lambda a: _has_number(a, VEGA), id="native-pdf-bytes"),
]


@pytest.mark.parametrize("mode_label, question, check", POINT_IN_TIME)
def test_point_in_time_aggregation(chat, record, mode_label, question, check):
    """Pin one single-document mode, ask a snapshot aggregation, assert the golden
    figure comes back through the real UI + Bedrock."""
    answer = chat.choose_mode(mode_label).ask(question)
    ok = check(answer)
    record(f"point-in-time · {mode_label}", question, answer, chat.last_tokens, ok)
    assert ok, f"[{mode_label}] unexpected answer:\n{answer}"


# ----------------------------------------------- temporal, across documents ---
def test_temporal_across_documents(chat, record, seeded_corpus):
    """Pin the whole-corpus mode and ask a per-report-date question. A correct
    answer names several distinct weekly report dates — proof it grouped ACROSS
    documents (the pre-seeded multi-week corpus), not just the one upload."""
    question = ("For each weekly report, what was the total vega? "
                "List the report_date alongside each total.")
    answer = chat.choose_mode("WHOLE corpus").ask(question)
    hits = [d for d in seeded_corpus["dates"] if d in answer]
    ok = len(hits) >= 2
    record("temporal · WHOLE corpus", question, answer, chat.last_tokens, ok)
    assert ok, (
        f"expected >=2 distinct report dates from {seeded_corpus['dates']}, "
        f"found {hits} in:\n{answer}"
    )
