"""pdf-test fixtures. `run_agent` drives one agent for a single turn and returns
its final text — used by the MockPdfLlm correctness tests, which build targeted
agents (model=MockPdfLlm()) rather than the import-bound root_agent.

Note: tests import `apps.pdf_insight.agent.root_agent` directly at module top.
The model binds lazily (shared.model.LazyModel resolves LLM_BACKEND per turn), so
the import no longer freezes the backend and the autouse `_backend_env` fixture in
the root conftest pinning LLM_BACKEND=mock at run time is sufficient. The old
purge+reimport helper (tests/pdf_insight/_graph.py) and the pdf_root_agent fixture
it backed are gone — see integration/test_agent_isolation.py for the regression
guard proving a single imported root_agent follows the runtime backend."""
import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path_factory, monkeypatch):
    """Redirect both backends' on-disk stores to a temp dir for every pdf test.

    The coordinator now ingests on a new PDF (into SQLite AND the corpus), so
    without this, coordinator-driven tests would write into the repo's real
    data/ (data/sqlite/*, data/pdf_corpus.duckdb). Tests that pin an explicit
    corpus_db/db_path in session state still override these (state > env)."""
    d = tmp_path_factory.mktemp("pdfstore")
    monkeypatch.setenv("PDF_SQLITE_DIR", str(d / "sqlite"))
    monkeypatch.setenv("PDF_CORPUS_DB", str(d / "corpus.duckdb"))


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
