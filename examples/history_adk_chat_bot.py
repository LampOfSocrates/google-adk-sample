"""basic_adk_chat_bot.py + a `history()` view of what ADK remembers.

    bot = HistoryAdkChatBot()
    bot.ask("My name is Sourav.")
    bot.ask("What's my name?")   # remembers — same session
    bot.history()                # dump the turns ADK has stored

Single-session, in-memory, synchronous `ask()` — same shape as BasicAdkChatBot.
The point of this one is `history()`: ADK's "memory" is literally the session's
accumulated event log, replayed to the model on every turn. history() reads that
log back out so you can see it.
"""
import asyncio
import time

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


class HistoryAdkChatBot:
    def __init__(
        self,
        instruction="You are a helpful assistant. Keep replies short.",
        model="gemini-2.0-flash",
        name="chat",
        app_name="myapp",
    ):
        self.app_name = app_name
        self._agent = LlmAgent(name=name, model=model, instruction=instruction)
        self._sessions = InMemorySessionService()
        self._runner = Runner(
            app_name=app_name, agent=self._agent, session_service=self._sessions
        )
        # One persistent loop drives the async session service + runner (the
        # InMemorySessionService must be created and used on the same loop).
        self._loop = asyncio.new_event_loop()
        self._user_id, self._session_id = "u1", "s1"
        self._loop.run_until_complete(
            self._sessions.create_session(
                app_name=app_name, user_id=self._user_id, session_id=self._session_id
            )
        )

    def ask(self, text):
        """Send one user turn; print the reply + per-turn stats; return the reply."""
        return self._loop.run_until_complete(self._ask(text))

    def history(self):
        """Print the stored conversation and return it as [{author, text}]."""
        return self._loop.run_until_complete(self._history())

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
        total = usage.total_token_count if usage else 0
        print(f"  [⏱ {time.perf_counter() - start:.2f}s · {total} tokens]")
        return answer

    async def _history(self):
        # Pull the session back out of the store — this IS ADK's memory.
        session = await self._sessions.get_session(
            app_name=self.app_name, user_id=self._user_id, session_id=self._session_id
        )
        turns = []
        print("\n--- session history ---")
        for event in session.events:
            if event.content and event.content.parts:
                text = "".join(p.text or "" for p in event.content.parts)
                if text.strip():
                    print(f"[{event.author}] {text}")
                    turns.append({"author": event.author, "text": text})
        print("--- end history ---\n")
        return turns


if __name__ == "__main__":
    bot = HistoryAdkChatBot()
    bot.ask("My name is Sourav.")
    bot.ask("What's my name?")  # same session -> remembers
    bot.history()
