"""Thin HTTP client for the FastAPI agent server (backend/server.py).

The Streamlit app does *everything* through these calls — it holds no ADK objects
and touches no disk. REST for the CRUD bits; a synchronous SSE consumer
(`stream_message`) for the live turn, which works inside Streamlit's main-thread
rerun (no asyncio needed). Point at a different server with API_BASE_URL.
"""
from __future__ import annotations

import json
import os

import httpx

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=_TIMEOUT)


def health() -> bool:
    try:
        with _client() as c:
            return c.get("/health").json().get("ok", False)
    except Exception:
        return False


# --- apps / agents ---------------------------------------------------------
def list_apps() -> dict:
    with _client() as c:
        return c.get("/apps").json()


def get_agents(app: str, backend: str) -> dict:
    with _client() as c:
        return c.get(f"/apps/{app}/agents", params={"backend": backend}).json()


def get_overrides(app: str) -> dict:
    with _client() as c:
        return c.get(f"/apps/{app}/overrides").json()


def put_overrides(app: str, body: dict) -> dict:
    with _client() as c:
        return c.put(f"/apps/{app}/overrides", json=body).json()


def delete_overrides(app: str) -> dict:
    with _client() as c:
        return c.delete(f"/apps/{app}/overrides").json()


# --- sessions / chat -------------------------------------------------------
def create_session(app: str, backend: str) -> str:
    with _client() as c:
        return c.post(f"/apps/{app}/sessions", json={"backend": backend}).json()["session_id"]


def stream_message(app: str, session_id: str, message: str, pdf_mode: str | None = None):
    """Yield wire frames (dicts) from the SSE turn stream as they arrive."""
    payload = {"message": message, "pdf_mode": pdf_mode}
    with _client() as c:
        with c.stream("POST", f"/apps/{app}/sessions/{session_id}/messages",
                      json=payload) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])


# --- pdf_insight -----------------------------------------------------------
def upload_pdf(filename: str, data: bytes) -> dict:
    with _client() as c:
        return c.post("/apps/pdf_insight/uploads",
                      files={"file": (filename, data, "application/pdf")}).json()


def pdf_schema() -> dict:
    with _client() as c:
        return c.get("/apps/pdf_insight/schema").json()


# --- conversations ---------------------------------------------------------
def list_conversations() -> list[dict]:
    with _client() as c:
        return c.get("/conversations").json()["conversations"]


def save_conversation(payload: dict) -> dict:
    with _client() as c:
        return c.post("/conversations", json=payload).json()


def load_conversation(conv_id: str) -> dict:
    with _client() as c:
        return c.get(f"/conversations/{conv_id}").json()


def delete_conversation(conv_id: str) -> dict:
    with _client() as c:
        return c.delete(f"/conversations/{conv_id}").json()
