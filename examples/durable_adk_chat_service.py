"""Multi-user ADK chat service whose conversations SURVIVE RESTARTS (SQLite).

This is adk_chat_service.py made durable. The architecture is identical — one
shared service, users/conversations separated by (user_id, session_id) — and the
ONLY substantive change is the session store:

    InMemorySessionService()                              # adk_chat_service.py: wiped on restart
    DatabaseSessionService("sqlite+aiosqlite:///./adk_sessions.db")   # this file: on disk

Because history now lives in SQLite, a client that keeps its session_id can stop,
the server can restart, and the next `ask()` still remembers earlier turns — they
are loaded back from the database. Run it with:

    uvicorn examples.durable_adk_chat_service:app --reload      # needs GOOGLE_API_KEY
"""
import time

from fastapi import FastAPI, HTTPException
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import BaseModel

# Any SQLAlchemy URL works, but ADK drives the DB with an ASYNC engine, so the
# driver must be async too: SQLite -> sqlite+aiosqlite (needs `pip install
# aiosqlite`); Postgres -> postgresql+asyncpg. SQLite (a single file) is perfect for
# one process / the demo; for multiple backend replicas point this at a shared async
# Postgres URL instead and NOTHING else in this file changes.
DB_URL = "sqlite+aiosqlite:///./adk_sessions.db"


class DurableAdkChatService:
    """One shared instance for the whole backend; conversations persisted to a DB.

    Build it once at startup. Users/conversations are separated by the (user_id,
    session_id) passed per call, never by separate objects — same as the in-memory
    version. The difference is that this store outlives the process.
    """

    def __init__(
        self,
        instruction="You are a helpful assistant.",
        model="gemini-2.0-flash",
        app_name="myapp",
        db_url=DB_URL,
    ):
        self.app_name = app_name
        self._agent = LlmAgent(name="chat", model=model, instruction=instruction)
        # The one line that makes it durable. DatabaseSessionService creates its
        # tables on first use, so no manual migration is needed for the demo.
        self._sessions = DatabaseSessionService(db_url=db_url)
        self._runner = Runner(
            app_name=app_name, agent=self._agent, session_service=self._sessions
        )

    async def start_conversation(self, user_id: str) -> str:
        """Open a NEW conversation and return its session_id (an ADK-minted uuid).

        The row is written to the DB immediately, so the id is valid across
        restarts. Hand it to the client to send on every later message."""
        session = await self._sessions.create_session(
            app_name=self.app_name, user_id=user_id
        )
        return session.id

    async def ask(self, user_id: str, session_id: str, text: str) -> dict:
        """Run one turn in an existing conversation; return the reply + turn stats.

        The runner loads the conversation's prior turns from the DB before calling
        the model and writes the new turns back — that round-trip is what gives
        restart-surviving memory. An unknown session_id raises KeyError (-> 404)."""
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

    async def history(self, user_id: str, session_id: str) -> list[dict]:
        """Read a conversation back out of the DB — proves it persisted. Returns
        [{author, text}] for each turn that carried text."""
        session = await self._sessions.get_session(
            app_name=self.app_name, user_id=user_id, session_id=session_id
        )
        if session is None:
            raise KeyError(session_id)
        out = []
        for event in session.events:
            if event.content and event.content.parts:
                text = "".join(p.text or "" for p in event.content.parts)
                if text.strip():
                    out.append({"author": event.author, "text": text})
        return out


# --------------------------------------------------------------------------- #
# FastAPI stub — the thin transport over the shared, durable service.
# --------------------------------------------------------------------------- #
service = DurableAdkChatService()
app = FastAPI(title="Durable ADK chat service")


class StartReq(BaseModel):
    user_id: str  # from your auth layer in real life, not the request body


class ChatReq(BaseModel):
    user_id: str
    session_id: str
    text: str


@app.post("/conversations")
async def start(req: StartReq):
    """Open a new (persisted) conversation -> client stores the session_id."""
    return {"session_id": await service.start_conversation(req.user_id)}


@app.post("/chat")
async def chat(req: ChatReq):
    """Send one message into an existing conversation (by session_id)."""
    try:
        return await service.ask(req.user_id, req.session_id, req.text)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown session_id; POST /conversations first")


@app.get("/conversations/{session_id}/history")
async def history(session_id: str, user_id: str):
    """Dump a conversation's stored turns — survives restarts (read from SQLite)."""
    try:
        return {"turns": await service.history(user_id, session_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown session_id")


# Durability demo — note the RESTART in the middle:
#
#   curl -s localhost:8000/conversations -H 'content-type: application/json' \
#        -d '{"user_id":"alice"}'                       # -> {"session_id":"<sid>"}
#   curl -s localhost:8000/chat -H 'content-type: application/json' \
#        -d '{"user_id":"alice","session_id":"<sid>","text":"My name is Sourav."}'
#   # ^C the server, restart it (the SQLite file adk_sessions.db stays on disk) ...
#   curl -s localhost:8000/chat -H 'content-type: application/json' \
#        -d '{"user_id":"alice","session_id":"<sid>","text":"What is my name?"}'  # still remembers
#   curl -s "localhost:8000/conversations/<sid>/history?user_id=alice"            # the stored turns
#
# Scaling: SQLite is a single file -> one writer, great for a single instance. For
# several replicas behind a load balancer, swap DB_URL to a shared async Postgres
# (e.g. "postgresql+asyncpg://user:pw@host/db"); the agent/runner stay
# one-per-process and every replica reads the same durable store.
# --------------------------------------------------------------------------- #
