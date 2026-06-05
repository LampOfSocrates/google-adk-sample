"""Shared test setup. Living at the project root puts the repo on sys.path so
tests can `import weather_agent`, and loads the .env so live tests get the key.
"""
import asyncio

import pytest
from dotenv import load_dotenv
from google.adk.runners import InMemoryRunner
from google.genai import types

load_dotenv()

APP_NAME = "weather_agent"


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
