"""The basic ADK chat, wrapped in a class with a synchronous `ask()`.

    bot = BasicAdkChatBot()
    bot.ask("My name is Sourav.")
    bot.ask("What's my name?")   # remembers — same session

Same idea as basic_adk_chat.py, but the async Runner/session plumbing is hidden
behind `ask()`, and one persistent session gives the bot memory across calls.
"""
import asyncio
import time

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


class BasicAdkChatBot:
    def __init__(
        self,
        instruction="You are a helpful assistant.",
        model="gemini-2.0-flash",
        name="chat",
    ):
        self._agent = LlmAgent(name=name, model=model, instruction=instruction)
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            app_name="myapp", agent=self._agent, session_service=self._session_service
        )
        # One persistent loop drives the async session service + runner. The
        # InMemorySessionService must be created AND used on the same loop, so we
        # can't use a fresh asyncio.run() per ask().
        self._loop = asyncio.new_event_loop()
        self._user_id, self._session_id = "u1", "s1"
        self._loop.run_until_complete(
            self._session_service.create_session(
                app_name="myapp", user_id=self._user_id, session_id=self._session_id
            )
        )

    def ask(self, text):
        """Send one user turn; print the reply + per-turn stats; return the reply."""
        return self._loop.run_until_complete(self._ask(text))

    async def _ask(self, text):
        msg = types.Content(role="user", parts=[types.Part(text=text)])
        start = time.perf_counter()
        answer, usage = "", None  # final model round-trip carries the token counts
        async for event in self._runner.run_async(
            user_id=self._user_id, session_id=self._session_id, new_message=msg
        ):
            if event.usage_metadata:
                usage = event.usage_metadata
            if event.is_final_response():
                answer = event.content.parts[0].text
                print(answer)
        latency = time.perf_counter() - start
        total = usage.total_token_count if usage else 0
        print(f"  [⏱ {latency:.2f}s · {total} tokens]")
        return answer


if __name__ == "__main__":
    bot = BasicAdkChatBot()
    bot.ask("My name is Sourav.")
    bot.ask("What's my name?")  # same session -> remembers
