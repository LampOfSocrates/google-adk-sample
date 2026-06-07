"""pdf-test fixtures. `run_agent` drives one agent for a single turn and returns
its final text — used by the MockPdfLlm correctness tests, which build targeted
agents (model=MockPdfLlm()) rather than the import-bound root_agent."""
import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types


@pytest.fixture
def run_agent():
    async def _run(agent, message: str, state: dict | None = None) -> str:
        runner = InMemoryRunner(agent=agent, app_name="pdf_test")
        session = await runner.session_service.create_session(
            app_name="pdf_test", user_id="u", state=state or {}
        )
        final = ""
        msg = types.Content(role="user", parts=[types.Part(text=message)])
        async for ev in runner.run_async(user_id="u", session_id=session.id, new_message=msg):
            if ev.is_final_response() and ev.content and ev.content.parts:
                final = ev.content.parts[0].text or final
        return final

    return _run
