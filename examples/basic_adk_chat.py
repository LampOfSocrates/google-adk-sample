import asyncio
import time

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

agent = LlmAgent(
    name="chat",
    model="gemini-2.0-flash",
    instruction="You are a helpful assistant.",
)

session_service = InMemorySessionService()
runner = Runner(app_name="myapp", agent=agent, session_service=session_service)


async def main():
    await session_service.create_session(
        app_name="myapp", user_id="u1", session_id="s1"
    )

    async def say(text):
        msg = types.Content(role="user", parts=[types.Part(text=text)])
        start = time.perf_counter()
        usage = None  # the final model round-trip carries the turn's token counts
        async for event in runner.run_async(
            user_id="u1", session_id="s1", new_message=msg
        ):
            if event.usage_metadata:
                usage = event.usage_metadata
            if event.is_final_response():
                print(event.content.parts[0].text)
        latency = time.perf_counter() - start
        total = usage.total_token_count if usage else 0
        print(f"  [⏱ {latency:.2f}s · {total} tokens]")

    await say("My name is Sourav.")
    await say("What's my name?")  # answers correctly — same session_id = memory


if __name__ == "__main__":
    asyncio.run(main())
