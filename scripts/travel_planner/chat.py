"""Tiny terminal REPL — talks to the agent server over HTTP.

    ./local_run.sh server                         # start the server first
    python scripts/travel_planner/chat.py

The server (backend/server.py) owns the ADK runner; this script is a thin client.
The backend is chosen with LLM_BACKEND (mock by default) and passed to the server
when the session is created; state — like your unit preference — persists across
turns within the one session. Point at another server with API_BASE_URL. Ctrl-C or
'quit' to exit.
"""
import os
import sys

# Make `from apps.pages import api_client` resolve when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from apps.pages import api_client  # noqa: E402  (pure-httpx client SDK; no ADK)

APP = "travel_planner"


def main():
    if not api_client.health():
        sys.exit(f"server unreachable at {api_client.BASE_URL} — start it with "
                 "`./local_run.sh server`")
    backend = os.environ.get("LLM_BACKEND", "mock")
    session_id = api_client.create_session(APP, backend)
    print(f"backend={backend}  server={api_client.BASE_URL}  (type 'quit' to exit)\n")
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
        res = api_client.run_turn(APP, session_id, text)
        print(f"bot > {res['error'] or res['text']}\n")


if __name__ == "__main__":
    main()
