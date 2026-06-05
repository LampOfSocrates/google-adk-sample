"""One live smoke test per agent against a chosen LLM backend.

    python scripts/smoke.py deepseek
    python scripts/smoke.py bedrock
    python scripts/smoke.py            # defaults to deepseek

Makes real (usually cheap) API calls. Each agent is isolated so one failure
doesn't block the rest. Prints PASS/FAIL plus the reply or the error.
"""
import asyncio
import os
import sys
import traceback

import certifi

# Stray SSL_CERT_FILE on this box breaks the HTTPS clients; force certifi.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

# Backend MUST be set before importing any agent (model binds at import time).
BACKEND = sys.argv[1] if len(sys.argv) > 1 else "deepseek"
os.environ["LLM_BACKEND"] = BACKEND

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402


async def _run(agent, prompt, app):
    runner = InMemoryRunner(agent=agent, app_name=app)
    session = await runner.session_service.create_session(app_name=app, user_id="smoke")
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])
    final = ""
    async for ev in runner.run_async(
        user_id="smoke", session_id=session.id, new_message=msg
    ):
        if ev.is_final_response() and ev.content and ev.content.parts:
            final = ev.content.parts[0].text or final
    return final


async def smoke(label, import_agent, prompt, expect=None):
    print(f"\n=== {label} === (prompt: {prompt!r})")
    try:
        agent = import_agent()
        reply = await asyncio.wait_for(_run(agent, prompt, label), timeout=120)
        ok = bool(reply and reply.strip())
        if expect:
            ok = ok and expect.lower() in reply.lower()
        print(f"[{'PASS' if ok else 'CHECK'}] {label}")
        print("  reply:", (reply or "")[:400])
    except Exception as e:  # noqa: BLE001 - smoke test: report, don't crash
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        traceback.print_exc()


def _travel():
    from apps.travel_planner.agent import root_agent
    return root_agent


def _diagram():
    from apps.text_to_diagram.agent import root_agent
    return root_agent


def _pdf():
    from apps.pdf_insight.agent import root_agent
    return root_agent


async def main():
    print("backend =", os.environ["LLM_BACKEND"])
    await smoke("travel_planner", _travel, "What's the weather in Tokyo?", expect="tokyo")
    await smoke("text_to_diagram", _diagram, "Paris is the capital of France.",
                expect="```mermaid")
    await smoke("pdf_insight", _pdf, "What is in this statement?")


if __name__ == "__main__":
    asyncio.run(main())
