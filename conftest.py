"""Shared test setup. Living at the project root puts the repo on sys.path so
tests can `import weather_agent`, and loads the .env so live tests get the key.
"""
import asyncio
import os

import pytest
from dotenv import load_dotenv
from google.adk.runners import InMemoryRunner
from google.genai import types

load_dotenv()

APP_NAME = "weather_agent"


def pytest_addoption(parser):
    """`--backend` chooses the LLM for live tests without remembering env vars.

        pytest -m live --backend deepseek

    The offline tests (diagram/pdf) force LLM_BACKEND=mock at import, which would
    clobber a shell LLM_BACKEND set for live runs. So we stash the live choice in
    a SEPARATE var (LIVE_BACKEND) that those modules never touch; the live test
    modules read it back just before importing their agent.
    """
    parser.addoption(
        "--backend",
        default=None,
        help="LLM backend for live tests: gemini|openai|deepseek|bedrock "
        "(default: gemini, or the current LLM_BACKEND if already non-mock).",
    )


def pytest_configure(config):
    """Freeze the live backend BEFORE any test module is imported (and thus
    before the offline modules set LLM_BACKEND=mock during collection)."""
    chosen = config.getoption("--backend") or os.environ.get("LLM_BACKEND", "")
    if chosen.strip().lower() in ("", "mock"):
        chosen = "gemini"
    os.environ["LIVE_BACKEND"] = chosen


async def _ask(runner, session_id, text, retries=4):
    """Send one user message and return the agent's final text reply.

    The free tier allows only ~5 requests/minute and a turn makes several calls,
    so we back off and retry on a 429 instead of failing the test outright.
    """
    msg = types.Content(role="user", parts=[types.Part(text=text)])
    for attempt in range(retries):
        try:
            final = ""
            async for event in runner.run_async(
                user_id="test", session_id=session_id, new_message=msg
            ):
                if event.is_final_response() and event.content:
                    final = event.content.parts[0].text
            return final
        except Exception as e:  # noqa: BLE001 - retry only on quota errors
            if "RESOURCE_EXHAUSTED" in str(e) and attempt < retries - 1:
                await asyncio.sleep(20)
            else:
                raise


@pytest.fixture(autouse=True)
def _backend_env(request, monkeypatch):
    """Pin LLM_BACKEND per test so the suite is hermetic.

    Test modules mutate this global at import time (offline -> mock, live -> the
    live backend). With randomized collection order, whichever module imports
    last would otherwise decide the *runtime* value for every test — which flakes
    any agent that reads the backend at run time (e.g. pdf_insight's native-PDF
    refusal). Agents bind their model at import; this only fixes runtime reads.
    """
    live = request.node.get_closest_marker("live")
    monkeypatch.setenv(
        "LLM_BACKEND",
        os.environ.get("LIVE_BACKEND", "gemini") if live else "mock",
    )


@pytest.fixture
def converse():
    """Run a list of user messages through one shared session.

    Returns (answers, final_state) so tests can assert on both the replies and
    the session state that the tools wrote.
    """

    async def _converse(agent, messages):
        runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
        session = await runner.session_service.create_session(
            app_name=APP_NAME, user_id="test"
        )
        answers = [await _ask(runner, session.id, m) for m in messages]
        refreshed = await runner.session_service.get_session(
            app_name=APP_NAME, user_id="test", session_id=session.id
        )
        return answers, dict(refreshed.state)

    return _converse
