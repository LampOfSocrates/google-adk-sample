import asyncio

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

APP_NAME = "myapp"
USER_ID = "u1"
SESSION_ID = "s1"

agent = LlmAgent(
    name="chat",
    model="gemini-2.0-flash",
    instruction="You are a helpful assistant. Keep replies short.",
)

session_service = InMemorySessionService()
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
    """Read the conversation back out of the session — this *is* ADK's memory."""
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


async def main():
    await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID
    )

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
