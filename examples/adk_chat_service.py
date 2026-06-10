"""Multi-user ADK chat as a backend service.

The key idea (and the difference from basic_adk_chat_bot.py): you do NOT create one
object per user. The `Agent` is stateless config and the `Runner` / session service
are reentrant, so the whole backend shares ONE `AdkChatService`. Users and their
separate conversations are told apart by the `user_id` + `session_id` you pass into
each call — not by separate Python objects.

    one shared service  ->  agent + runner + session_service   (created once)
    per user            ->  user_id        (from your auth layer)
    per conversation    ->  session_id      (one per chat; a user can have many)

All history/state lives in the session_service, keyed by (app_name, user_id,
session_id). Run the demo server with:

    uvicorn examples.adk_chat_service:app --reload      # needs a GOOGLE_API_KEY
"""
import time

from fastapi import FastAPI, HTTPException
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel


class AdkChatService:
    """One instance for the entire backend.

    Concurrency: the runner is reentrant across sessions, and each call is
    scoped by (user_id, session_id), so concurrent users don't interfere. Do NOT
    instantiate this per request — build it once at startup and share it.
    """

    def __init__(
        self,
        instruction="You are a helpful assistant.",
        model="gemini-2.0-flash",
        app_name="myapp",
    ):
        self.app_name = app_name
        # Stateless config: defines WHAT the assistant is. Shared by everyone.
        self._agent = LlmAgent(name="chat", model=model, instruction=instruction)
        # The conversation store. InMemory = per-process and wiped on restart —
        # fine for a demo. For production swap in DatabaseSessionService or
        # VertexAiSessionService (see "Scaling" at the bottom) — the rest is identical.
        self._sessions = InMemorySessionService()
        # The execution engine: drives the agent loop and reads/writes the store.
        # One runner serves all users; it takes user_id + session_id per call.
        self._runner = Runner(
            app_name=app_name, agent=self._agent, session_service=self._sessions
        )

    async def start_conversation(self, user_id: str) -> str:
        """Begin a NEW conversation for a user and return its session_id.

        Hand this id back to the client; it must send it on every subsequent
        message so the turns accumulate into one remembered conversation. Omitting
        an explicit id lets ADK mint a collision-free uuid for us."""
        session = await self._sessions.create_session(
            app_name=self.app_name, user_id=user_id
        )
        return session.id

    async def ask(self, user_id: str, session_id: str, text: str) -> dict:
        """Run one turn in an EXISTING conversation; return the reply + turn stats.

        The session must already exist (created via start_conversation) — with the
        in-memory store an unknown id raises. We surface that as a clean 404 in the
        endpoint below rather than letting it 500."""
        session = await self._sessions.get_session(
            app_name=self.app_name, user_id=user_id, session_id=session_id
        )
        if session is None:
            raise KeyError(session_id)

        msg = types.Content(role="user", parts=[types.Part(text=text)])
        start = time.perf_counter()
        reply, usage = "", None  # the final model round-trip carries token counts
        async for event in self._runner.run_async(
            user_id=user_id, session_id=session_id, new_message=msg
        ):
            if event.usage_metadata:
                usage = event.usage_metadata
            if event.is_final_response():
                reply = event.content.parts[0].text
        return {
            "reply": reply,
            "tokens": usage.total_token_count if usage else 0,
            "latency_s": round(time.perf_counter() - start, 3),
        }


# --------------------------------------------------------------------------- #
# FastAPI stub — the thin transport layer over the shared service.
# --------------------------------------------------------------------------- #
# Built ONCE at import. In a larger app you'd manage this via FastAPI's lifespan
# and stash it on app.state instead of a module global, but the lifecycle is the
# same: one service for the process.
service = AdkChatService()
app = FastAPI(title="ADK chat service")


class StartReq(BaseModel):
    user_id: str  # in real life this comes from auth (a JWT/session cookie), not the body


class ChatReq(BaseModel):
    user_id: str
    session_id: str
    text: str


@app.post("/conversations")
async def start(req: StartReq):
    """Open a new conversation -> the client stores the returned session_id."""
    session_id = await service.start_conversation(req.user_id)
    return {"session_id": session_id}


@app.post("/chat")
async def chat(req: ChatReq):
    """Send one message into an existing conversation (identified by session_id)."""
    try:
        return await service.ask(req.user_id, req.session_id, req.text)
    except KeyError:
        # Unknown/expired session — tell the client to start a new conversation.
        raise HTTPException(status_code=404, detail="unknown session_id; POST /conversations first")


# Two users, each with their own session, are fully isolated even though they share
# the one `service`:
#
#   curl -s localhost:8000/conversations -H 'content-type: application/json' \
#        -d '{"user_id":"alice"}'                      # -> {"session_id":"<sid_a>"}
#   curl -s localhost:8000/chat -H 'content-type: application/json' \
#        -d '{"user_id":"alice","session_id":"<sid_a>","text":"My name is Sourav."}'
#   curl -s localhost:8000/chat -H 'content-type: application/json' \
#        -d '{"user_id":"alice","session_id":"<sid_a>","text":"What is my name?"}'  # remembers
#   # a different (user_id, session_id) -> a separate, isolated conversation.
#
# --------------------------------------------------------------------------- #
# Scaling notes (why InMemory is demo-only):
#   * Restart wipes all conversations. Use a durable session service for real use.
#   * Multiple backend replicas behind a load balancer DON'T share an in-memory
#     store, so a follow-up request landing on another replica won't find the
#     session. A DB-backed/Vertex session service is shared infra any replica can
#     read — the agent/runner stay one-per-process; only the store is shared.
#   * Consider GC of idle sessions, and rate limiting per user_id.
# --------------------------------------------------------------------------- #
