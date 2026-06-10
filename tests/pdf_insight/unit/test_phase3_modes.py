"""Phase 3 — mode resolution precedence (request > session > env > auto). No LLM."""
from apps.pdf_insight import config
from apps.pdf_insight.tools import set_pdf_mode


def test_default_is_auto():
    assert config.resolve_mode(None, {}, {}) == config.AUTO


def test_env_is_the_baseline():
    assert config.resolve_mode(None, {}, {"PDF_MODE": config.SQL_FROM_TEXT}) == config.SQL_FROM_TEXT


def test_session_overrides_env():
    out = config.resolve_mode(
        None, {"pdf_mode": config.ALL_TABLES_AS_TEXT}, {"PDF_MODE": config.SQL_FROM_TEXT}
    )
    assert out == config.ALL_TABLES_AS_TEXT


def test_request_overrides_session_and_env():
    out = config.resolve_mode(
        config.SOME_TABLES_AS_TEXT,
        {"pdf_mode": config.ALL_TABLES_AS_TEXT},
        {"PDF_MODE": config.SQL_FROM_TEXT},
    )
    assert out == config.SOME_TABLES_AS_TEXT


def test_unknown_pin_is_ignored():
    assert config.resolve_mode("NONSENSE", {"pdf_mode": "ALSO_BAD"}, {}) == config.AUTO


def test_parse_request_directive():
    assert config.parse_request_override("answer in mode: LLM_MAKES_SQL_FROM_CHAT please") == config.SQL_FROM_TEXT
    assert config.parse_request_override("mode=auto") == config.AUTO
    assert config.parse_request_override("no directive here") is None
    assert config.parse_request_override("mode: not_a_mode") is None


class _Ctx:
    def __init__(self, state=None):
        self.state = state or {}


def test_set_pdf_mode_tool_writes_session_state():
    ctx = _Ctx()
    assert set_pdf_mode(config.SQL_FROM_TEXT, ctx)["status"] == "success"
    assert ctx.state["pdf_mode"] == config.SQL_FROM_TEXT
    assert set_pdf_mode("bogus", ctx)["status"] == "error"
