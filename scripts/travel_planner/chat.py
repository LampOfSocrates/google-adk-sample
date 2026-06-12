"""Tiny REPL to talk to the root agent from the terminal.

    python scripts/travel_planner/chat.py

Uses whatever LLM_BACKEND is set in .env (mock by default, so it's free and
offline). Keeps one session so state — like your unit preference — persists
across turns. Ctrl-C or 'quit' to exit.
"""
import asyncio
import os
import sys

import certifi

# Stray SSL_CERT_FILE on this box breaks the HTTPS clients; force certifi.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

# Make `import backend.travel_planner` work when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

from google.adk.runners import InMemoryRunner  # noqa: E402  (after load_dotenv)
from google.genai import types  # noqa: E402

from backend.travel_planner.agent import root_agent  # noqa: E402

APP = "travel_planner"


async def main():
    runner = InMemoryRunner(agent=root_agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="you")
    print(f"backend={os.environ.get('LLM_BACKEND', 'mock')}  (type 'quit' to exit)\n")
    while True:
        try:
            text = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in ("quit", "exit"):
            break
        if not text:
            continue
        msg = types.Content(role="user", parts=[types.Part(text=text)])
        reply = ""
        async for event in runner.run_async(
            user_id="you", session_id=session.id, new_message=msg
        ):
            if event.is_final_response() and event.content:
                reply = event.content.parts[0].text
        print(f"bot > {reply}\n")


if __name__ == "__main__":
    asyncio.run(main())
