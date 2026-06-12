"""FastAPI server — the single backend the Streamlit client talks to.

Exposes the agents over a REST + SSE API (OpenAPI docs at `/docs`):
  * run a turn and stream UIEvents (SSE),
  * inspect / edit each agent's prompt & model (overlays),
  * ingest PDFs and read the queryable schema,
  * save / load / delete conversations.

The ADK runners, sessions, and all `data/` persistence live here; the client holds
no ADK objects and touches no disk. Run with:

    uvicorn backend.server:app --reload --port 8000
"""
from __future__ import annotations

import json
import os

import certifi

# Match the rest of the repo: a stray SSL_CERT_FILE breaks the HTTPS clients.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ.pop("SSL_CERT_DIR", None)

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend import conversations, overrides, registry  # noqa: E402
from backend.pdf_insight.ingest import ingest_pdf_everywhere  # noqa: E402
from backend.pdf_insight.stores import SQLiteStore, get_corpus_store  # noqa: E402
from backend.service import RunnerManager  # noqa: E402

app = FastAPI(title="ADK Agent Server", version="1.0")
manager = RunnerManager()

UPLOAD_DIR = os.path.join("data", "uploads")
# Single active PDF for the single-doc modes (mirrors the old UI's per-session
# pdf_state; one user, one active PDF on this dev server).
_pdf_state: dict = {}


# --- wire models -----------------------------------------------------------
class CreateSession(BaseModel):
    backend: str = "mock"


class Message(BaseModel):
    message: str
    pdf_mode: str | None = None


class SaveConversation(BaseModel):
    id: str | None = None
    app: str
    backend: str
    title: str | None = None
    messages: list[dict] = []
    debug_turns: list[dict] = []
    extra: dict | None = None


# --- apps + agents ---------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/apps")
def list_apps():
    return {"apps": list(registry.APPS), "backends": registry.BACKENDS}


@app.get("/apps/{app}/agents")
def get_agents(app: str, backend: str = "mock"):
    _require_app(app)
    return manager.agents(app, backend)


@app.get("/apps/{app}/overrides")
def get_overrides(app: str):
    _require_app(app)
    return overrides.load(app)


@app.put("/apps/{app}/overrides")
def put_overrides(app: str, body: dict):
    _require_app(app)
    overrides.save(app, body)
    manager.rebuild(app)
    return {"ok": True, "saved": overrides.load(app)}


@app.delete("/apps/{app}/overrides")
def delete_overrides(app: str):
    _require_app(app)
    overrides.clear(app)
    manager.rebuild(app)
    return {"ok": True}


# --- sessions + chat -------------------------------------------------------
@app.post("/apps/{app}/sessions")
async def create_session(app: str, body: CreateSession):
    _require_app(app)
    sid = await manager.create_session(app, body.backend)
    return {"session_id": sid}


@app.post("/apps/{app}/sessions/{sid}/messages")
async def send_message(app: str, sid: str, body: Message):
    _require_app(app)

    async def gen():
        try:
            async for frame in manager.run(sid, body.message, body.pdf_mode):
                yield f"data: {json.dumps(frame, default=str)}\n\n"
        except KeyError:
            err = {"kind": "error", "text": f"unknown session {sid}"}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- pdf_insight -----------------------------------------------------------
@app.post("/apps/pdf_insight/uploads")
async def upload_pdf(file: UploadFile = File(...)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = os.path.join(UPLOAD_DIR, file.filename)
    with open(path, "wb") as out:
        out.write(await file.read())
    summary = ingest_pdf_everywhere(path, _pdf_state)
    os.environ["PDF_PATH"] = path  # the coordinator's default active PDF
    return {"file": file.filename, "summary": summary}


@app.get("/apps/pdf_insight/schema")
def pdf_schema():
    out: dict = {"active_pdf": os.environ.get("PDF_PATH"), "corpus": None, "sqlite": None}
    out["corpus"] = get_corpus_store(_pdf_state).list_schema()
    db = _pdf_state.get("db_path")
    if db:
        out["sqlite"] = SQLiteStore(db).list_schema()
    return out


# --- conversations ---------------------------------------------------------
@app.get("/conversations")
def conv_list():
    return {"conversations": conversations.list_conversations()}


@app.post("/conversations")
def conv_save(body: SaveConversation):
    conv_id = body.id or conversations.new_id(body.app, conversations.title_from(body.messages))
    folder = conversations.save(
        conv_id, app=body.app, backend=body.backend, messages=body.messages,
        debug_turns=body.debug_turns, title=body.title, extra=body.extra,
    )
    return {"id": conv_id, "folder": folder}


@app.get("/conversations/{conv_id}")
def conv_get(conv_id: str):
    return conversations.load(conv_id)


@app.delete("/conversations/{conv_id}")
def conv_delete(conv_id: str):
    conversations.delete(conv_id)
    return {"ok": True}


def _require_app(app: str) -> None:
    if app not in registry.APPS:
        raise HTTPException(status_code=404, detail=f"unknown app {app!r}")
