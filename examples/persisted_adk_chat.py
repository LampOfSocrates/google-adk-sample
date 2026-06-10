"""Like history_adk_chat.py, but the conversation survives restarts.

Swap InMemorySessionService -> DatabaseSessionService and the memory is now
backed by SQLite. Run this, chat, quit, run it again with the same SESSION_ID:
the agent still remembers earlier turns because they were persisted to disk.
"""

import asyncio

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

APP_NAME = "myapp"
USER_ID = "u1"
SESSION_ID = "s1"
DB_URL = "sqlite:///./adk_chat.db"

agent = LlmAgent(
    name="chat",
    model="gemini-2.0-flash",
    instruction="You are a helpful assistant. Keep replies short.",
)

# Only this line changes vs. the in-memory version — everything below is identical.
session_service = DatabaseSessionService(db_url=DB_URL)
runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)


async def say(text: str) -> str:
    """Send one user turn, return the agent's final text reply."""
    msg = types.Content(role="user", parts=[types.Part(text=text)])
    reply = ""
    async for event in runner.run_async(
        user_id=USER_ID, session_id=SESSION_ID, new_message=msg
    ):
        if event.is_final_response():
            reply = event.content.parts[0].text
    return reply


async def dump_history() -> None:
    """Read the conversation back out of the session — now loaded from SQLite."""
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID
    )
    print("\n--- session history ---")
    for event in session.events:
        if event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts)
            if text.strip():
                print(f"[{event.author}] {text}")
    print("--- end history ---\n")


async def get_or_create_session() -> None:
    """Reuse the persisted session if it exists, otherwise start a fresh one."""
    existing = await session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID
    )
    if existing is None:
        await session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID
        )
        print("(started a new session)")
    else:
        print(f"(resumed session with {len(existing.events)} stored events)")


async def main():
    await get_or_create_session()

    print("Chat with the agent. Type 'history' to dump memory, 'quit' to exit.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in {"quit", "exit"}:
            break
        if text.lower() == "history":
            await dump_history()
            continue

        reply = await say(text)
        print(f"bot> {reply}")


if __name__ == "__main__":
    asyncio.run(main())
