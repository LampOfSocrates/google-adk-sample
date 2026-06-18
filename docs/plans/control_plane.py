"""
Nabu Agent Control Plane — core domain, store interface, ADK bridge,
ops (chat/replay/fork), and FastAPI routes.

Naming convention:
  - Nabu-prefixed classes are our own.
  - Where we wrap ADK primitives, the docstring on the class names the wrapped type.
  - Runner.app_name is always set to NabuAppVariant.id (see adk_app_name helper).
"""

from __future__ import annotations

import asyncio
import importlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Literal, Protocol
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.genai import types


# ============================================================
# Domain
# ============================================================

@dataclass(frozen=True)
class NabuApp:
    # A registered agentic product; the top-level container for variants.
    id: str
    name: str
    slug: str
    owner: str
    default_variant_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class NabuAppVariant:
    # An immutable, versioned agent spec; the unit of A/B testing under a NabuApp.
    id: str
    nabu_app_id: str
    entrypoint: str                  # "support_bot.v2_terse:build_agent"
    config: dict                     # prompts, model, tools, params
    code_ref: str                    # git sha or image digest
    schema_version: int              # gates state-copy between variants on fork
    display_name: str                # user-facing label; only mutable field
    archived: bool
    parent_variant_id: str | None
    notes: str
    created_by: str
    created_at: datetime


class ForkMode(str, Enum):
    # How a child NabuAppSession was derived from its parent.
    REPLAY_FROM_START = "replay_from_start"
    FORK_FROM_NOW = "fork_from_now"


class SessionStatus(str, Enum):
    # Lifecycle states for a NabuAppSession.
    ACTIVE = "active"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class NabuAppSession:
    # A chat session bound to one NabuAppVariant for its lifetime; wraps an ADK Session by id.
    id: str
    nabu_app_id: str
    variant_id: str
    user_id: str
    status: SessionStatus
    created_at: datetime
    parent_session_id: str | None = None
    fork_mode: ForkMode | None = None
    fork_point: int | None = None
    debug_mode: bool = False
    events_s3_uri: str | None = None


@dataclass(frozen=True)
class NabuMessage:
    # A user/assistant bubble in the chat transcript — aggregated from ADK Content/Part after a turn.
    session_id: str
    turn_index: int
    seq: int                         # 0=user, 1=assistant within a turn
    role: Literal["user", "assistant"]
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class NabuTraceEvent:
    # A persisted, indexed copy of one ADK Event (audit/trace), keyed by (session, turn, seq).
    session_id: str
    turn_index: int
    seq: int
    author: str
    payload: dict                    # ev.model_dump() of the ADK Event
    created_at: datetime = field(default_factory=datetime.utcnow)


class BulkJobStatus(str, Enum):
    # Lifecycle states for a BulkJob.
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class BulkJob:
    # A batch that replays N source sessions under one target variant, producing N clone sessions.
    id: str
    target_variant_id: str
    source_session_ids: list[str]
    created_session_ids: list[str]
    status: BulkJobStatus
    created_at: datetime
    completed: int = 0
    failed: int = 0


@dataclass(frozen=True)
class NabuToolSpec:
    # Catalog record for a tool a variant may invoke; runtime FunctionTool is built from this.
    name: str
    owner: str
    entrypoint: str                  # "support_bot.tools.lookup_order:lookup_order_tool"
    json_schema: dict
    schema_version: int
    allowed_variant_ids: list[str] | None  # None = open to all variants


@dataclass(frozen=True)
class Rubric:
    # A named scoring scale (e.g. 1-5, pass|fail) attached to Ratings.
    id: str
    name: str
    scale: str
    description: str
    created_at: datetime


@dataclass(frozen=True)
class Rating:
    # A score against a Rubric, attached to a session, turn, or message.
    id: str
    rubric_id: str
    target_type: Literal["session", "turn", "message"]
    target_id: str                   # session_id; "session_id:turn" for turn; message id otherwise
    score: str
    notes: str
    evaluator_id: str
    created_at: datetime


@dataclass(frozen=True)
class Comment:
    # Free-text annotation on a session, turn, or message.
    id: str
    target_type: Literal["session", "turn", "message"]
    target_id: str
    author_id: str
    body: str
    created_at: datetime


@dataclass(frozen=True)
class Share:
    # An explicit access grant of a NabuAppSession to a user, with a role.
    id: str
    session_id: str
    user_id: str
    role: Literal["viewer", "editor"]
    created_at: datetime


@dataclass(frozen=True)
class ShareLink:
    # A tokenized, revocable share link to a NabuAppSession.
    token: str
    session_id: str
    role: Literal["viewer", "editor"]
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class EvaluationCase:
    # A golden test case promoted from a real session, with expected output / assertions.
    id: str
    source_session_id: str
    turn_index: int | None
    name: str
    expected: dict                   # {contains?, equals?, regex?, …}
    created_at: datetime


@dataclass(frozen=True)
class EvaluationSuite:
    # A named, runnable collection of EvaluationCases.
    id: str
    name: str
    case_ids: list[str]
    created_at: datetime


# ============================================================
# Store — persistence boundary
# ============================================================

class Store(Protocol):
    # Persistence boundary for all control-plane data; Postgres impl lives in stores/postgres.py.

    # Apps
    async def get_app(self, app_id: str) -> NabuApp: ...

    # Variants
    async def get_variant(self, variant_id: str) -> NabuAppVariant: ...
    async def create_variant(self, variant: NabuAppVariant) -> None: ...
    async def list_variants(self, nabu_app_id: str) -> list[NabuAppVariant]: ...

    # Sessions
    async def create_app_session(self, session: NabuAppSession) -> None: ...
    async def get_app_session(self, session_id: str) -> NabuAppSession: ...
    async def update_session_status(self, session_id: str, status: SessionStatus) -> None: ...

    # Messages
    async def list_messages(self, session_id: str) -> list[NabuMessage]: ...
    async def append_message(self, msg: NabuMessage) -> None: ...

    # Trace events
    async def append_trace_event(self, event: NabuTraceEvent) -> None: ...
    async def list_trace_events(
        self, session_id: str, turn_index: int | None = None,
    ) -> list[NabuTraceEvent]: ...

    # Bulk jobs
    async def create_bulk_job(self, job: BulkJob) -> None: ...
    async def get_bulk_job(self, job_id: str) -> BulkJob: ...
    async def set_bulk_job_status(self, job_id: str, status: BulkJobStatus) -> None: ...
    async def increment_bulk_job(
        self, job_id: str, *, completed: int = 0, failed: int = 0,
    ) -> None: ...
    async def attach_session_to_bulk_job(self, job_id: str, session_id: str) -> None: ...


# ============================================================
# ProgressSink — pub/sub boundary
# ============================================================

class ProgressSink(Protocol):
    # Pub/sub boundary for background-job updates (Redis pub/sub, Postgres NOTIFY, SSE bus, …).
    async def publish(self, channel: str, payload: dict) -> None: ...


class NullProgressSink:
    # No-op ProgressSink for tests and synchronous code paths.
    async def publish(self, channel: str, payload: dict) -> None:
        return None


# ============================================================
# ADK bridge
# ============================================================

def adk_app_name(variant_id: str) -> str:
    # Centralized translation from NabuAppVariant.id to ADK's app_name argument.
    return variant_id


class RunnerCache:
    # Lazy, in-memory cache of ADK Runners keyed by NabuAppVariant.id.
    def __init__(self, session_service: BaseSessionService, store: Store):
        self._session_service = session_service
        self._store = store
        self._runners: dict[str, Runner] = {}

    async def get(self, variant_id: str) -> Runner:
        if variant_id in self._runners:
            return self._runners[variant_id]
        variant = await self._store.get_variant(variant_id)
        agent = _build_agent(variant)
        runner = Runner(
            agent=agent,
            app_name=adk_app_name(variant_id),
            session_service=self._session_service,
        )
        self._runners[variant_id] = runner
        return runner


def _build_agent(variant: NabuAppVariant):
    # Resolve "module.path:factory_fn" and call factory(variant.config) to build an ADK Agent.
    module_path, func_name = variant.entrypoint.split(":")
    module = importlib.import_module(module_path)
    factory = getattr(module, func_name)
    return factory(variant.config)


# ============================================================
# Ops — business logic (would live in service/src/ops/)
# ============================================================

async def run_turn(
    session_id: str,
    user_text: str,
    store: Store,
    runners: RunnerCache,
) -> AsyncIterator[dict]:
    """Live chat turn. Persists NabuMessage + NabuTraceEvent rows and yields SSE-shaped dicts."""
    session = await store.get_app_session(session_id)
    runner = await runners.get(session.variant_id)
    messages = await store.list_messages(session_id)
    turn_index = (messages[-1].turn_index + 1) if messages else 0

    await store.append_message(NabuMessage(
        session_id=session_id, turn_index=turn_index, seq=0,
        role="user", content=user_text,
    ))
    yield {"type": "user_message", "turn_index": turn_index, "content": user_text}

    assistant_text: list[str] = []
    tool_calls: list[dict] = []
    event_seq = 0

    async for ev in runner.run_async(
        user_id=session.user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=user_text)]),
    ):
        await store.append_trace_event(NabuTraceEvent(
            session_id=session_id, turn_index=turn_index, seq=event_seq,
            author=ev.author or "agent", payload=ev.model_dump(),
        ))
        event_seq += 1

        for fc in (ev.get_function_calls() or []):
            tool_calls.append({"name": fc.name, "args": dict(fc.args or {})})
            yield {
                "type": "tool_call", "turn_index": turn_index,
                "name": fc.name, "args": dict(fc.args or {}),
            }

        if ev.content and ev.content.parts:
            chunk = "".join(p.text or "" for p in ev.content.parts)
            if chunk and getattr(ev, "partial", False):
                yield {"type": "delta", "turn_index": turn_index, "text": chunk}
                assistant_text.append(chunk)
            elif chunk and ev.is_final_response() and not assistant_text:
                assistant_text.append(chunk)
                yield {"type": "delta", "turn_index": turn_index, "text": chunk}

    final = "".join(assistant_text)
    await store.append_message(NabuMessage(
        session_id=session_id, turn_index=turn_index, seq=1,
        role="assistant", content=final, tool_calls=tool_calls,
    ))
    yield {"type": "assistant_message", "turn_index": turn_index, "content": final}


async def replay_session(
    source_session_id: str,
    target_variant_id: str,
    user_id: str,
    store: Store,
    runners: RunnerCache,
    session_service: BaseSessionService,
    progress: ProgressSink = NullProgressSink(),
    bulk_job_id: str | None = None,
) -> str:
    """Replay every user turn of source under target_variant_id in a new NabuAppSession.
    Starts with clean ADK state. Publishes per-turn progress on a pub/sub channel."""
    source = await store.get_app_session(source_session_id)
    src_msgs = await store.list_messages(source_session_id)
    user_msgs = [m for m in src_msgs if m.role == "user"]

    new_id = str(uuid4())
    await store.create_app_session(NabuAppSession(
        id=new_id, nabu_app_id=source.nabu_app_id, variant_id=target_variant_id,
        user_id=user_id, status=SessionStatus.ACTIVE,
        created_at=datetime.utcnow(),
        parent_session_id=source_session_id,
        fork_mode=ForkMode.REPLAY_FROM_START,
        fork_point=0,
    ))

    await session_service.create_session(
        app_name=adk_app_name(target_variant_id),
        user_id=user_id,
        session_id=new_id,
    )

    runner = await runners.get(target_variant_id)
    channel = f"bulk:{bulk_job_id}" if bulk_job_id else f"session:{new_id}"
    total = len(user_msgs)

    try:
        for turn_index, msg in enumerate(user_msgs):
            await store.append_message(NabuMessage(
                session_id=new_id, turn_index=turn_index, seq=0,
                role="user", content=msg.content,
            ))

            assistant_text: list[str] = []
            tool_calls: list[dict] = []
            event_seq = 0

            async for ev in runner.run_async(
                user_id=user_id, session_id=new_id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=msg.content)],
                ),
            ):
                await store.append_trace_event(NabuTraceEvent(
                    session_id=new_id, turn_index=turn_index, seq=event_seq,
                    author=ev.author or "agent", payload=ev.model_dump(),
                ))
                event_seq += 1

                for fc in (ev.get_function_calls() or []):
                    tool_calls.append({"name": fc.name, "args": dict(fc.args or {})})

                if ev.content and ev.content.parts:
                    chunk = "".join(p.text or "" for p in ev.content.parts)
                    if chunk and getattr(ev, "partial", False):
                        assistant_text.append(chunk)
                    elif chunk and ev.is_final_response() and not assistant_text:
                        assistant_text.append(chunk)

            await store.append_message(NabuMessage(
                session_id=new_id, turn_index=turn_index, seq=1,
                role="assistant", content="".join(assistant_text),
                tool_calls=tool_calls,
            ))

            await progress.publish(channel, {
                "type": "turn_complete",
                "session_id": new_id,
                "turn_index": turn_index,
                "total": total,
                "bulk_job_id": bulk_job_id,
            })

        await store.update_session_status(new_id, SessionStatus.COMPLETE)
        await progress.publish(channel, {
            "type": "session_complete", "session_id": new_id, "bulk_job_id": bulk_job_id,
        })
    except Exception as e:
        await store.update_session_status(new_id, SessionStatus.FAILED)
        await progress.publish(channel, {
            "type": "session_failed", "session_id": new_id,
            "error": str(e), "bulk_job_id": bulk_job_id,
        })
        raise

    return new_id


async def fork_session_now(
    source_session_id: str,
    target_variant_id: str,
    user_id: str,
    store: Store,
    session_service: BaseSessionService,
    copy_state: bool = True,
) -> str:
    """Create a self-contained child NabuAppSession that inherits the parent's transcript
    up to now, then accepts new turns under a different variant."""
    source = await store.get_app_session(source_session_id)
    src_msgs = await store.list_messages(source_session_id)
    fork_point = (src_msgs[-1].turn_index + 1) if src_msgs else 0

    new_id = str(uuid4())
    await store.create_app_session(NabuAppSession(
        id=new_id, nabu_app_id=source.nabu_app_id, variant_id=target_variant_id,
        user_id=user_id, status=SessionStatus.ACTIVE,
        created_at=datetime.utcnow(),
        parent_session_id=source_session_id,
        fork_mode=ForkMode.FORK_FROM_NOW,
        fork_point=fork_point,
    ))

    for m in src_msgs:
        await store.append_message(NabuMessage(
            session_id=new_id, turn_index=m.turn_index, seq=m.seq,
            role=m.role, content=m.content, tool_calls=m.tool_calls,
        ))

    initial_state: dict = {}
    if copy_state:
        src_adk = await session_service.get_session(
            app_name=adk_app_name(source.variant_id),
            user_id=source.user_id,
            session_id=source_session_id,
        )
        if src_adk is not None:
            initial_state = dict(src_adk.state or {})

    await session_service.create_session(
        app_name=adk_app_name(target_variant_id),
        user_id=user_id,
        session_id=new_id,
        state=initial_state,
    )
    return new_id


# ============================================================
# Routes (subset — full set lives in service/src/api/)
# ============================================================

def build_router(
    store: Store,
    runners: RunnerCache,
    session_service: BaseSessionService,
    progress: ProgressSink,
) -> APIRouter:
    router = APIRouter()

    @router.post("/nabu-app-sessions")
    async def create_session(nabu_app_id: str, variant_id: str, user_id: str):
        new_id = str(uuid4())
        await store.create_app_session(NabuAppSession(
            id=new_id, nabu_app_id=nabu_app_id, variant_id=variant_id, user_id=user_id,
            status=SessionStatus.ACTIVE, created_at=datetime.utcnow(),
        ))
        await session_service.create_session(
            app_name=adk_app_name(variant_id), user_id=user_id, session_id=new_id,
        )
        return {"session_id": new_id}

    @router.post("/nabu-app-sessions/{session_id}/turn")
    async def post_turn(session_id: str, user_text: str):
        async def sse():
            async for event in run_turn(session_id, user_text, store, runners):
                yield f"data: {json.dumps(event)}\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream")

    @router.post("/nabu-app-sessions/{session_id}/replay")
    async def post_replay(session_id: str, target_variant_id: str, user_id: str):
        new_id = await replay_session(
            session_id, target_variant_id, user_id,
            store, runners, session_service, progress,
        )
        return {"session_id": new_id}

    @router.post("/nabu-app-sessions/{session_id}/fork")
    async def post_fork(
        session_id: str, target_variant_id: str, user_id: str, copy_state: bool = True,
    ):
        new_id = await fork_session_now(
            session_id, target_variant_id, user_id,
            store, session_service, copy_state=copy_state,
        )
        return {"session_id": new_id}

    @router.post("/bulk-jobs")
    async def post_bulk_job(
        target_variant_id: str, source_session_ids: list[str], user_id: str,
    ):
        job_id = str(uuid4())
        await store.create_bulk_job(BulkJob(
            id=job_id, target_variant_id=target_variant_id,
            source_session_ids=source_session_ids, created_session_ids=[],
            status=BulkJobStatus.PENDING, created_at=datetime.utcnow(),
        ))
        asyncio.create_task(_run_bulk_job(
            job_id, target_variant_id, source_session_ids, user_id,
            store, runners, session_service, progress,
        ))
        return {"bulk_job_id": job_id}

    @router.get("/bulk-jobs/{job_id}")
    async def get_bulk_job(job_id: str):
        return await store.get_bulk_job(job_id)

    return router


async def _run_bulk_job(
    job_id: str,
    target_variant_id: str,
    source_session_ids: list[str],
    user_id: str,
    store: Store,
    runners: RunnerCache,
    session_service: BaseSessionService,
    progress: ProgressSink,
    concurrency: int = 4,
):
    # Background worker: fans out replay_session across source ids with a semaphore.
    await store.set_bulk_job_status(job_id, BulkJobStatus.RUNNING)
    sem = asyncio.Semaphore(concurrency)

    async def one(src_id: str):
        async with sem:
            try:
                new_id = await replay_session(
                    src_id, target_variant_id, user_id,
                    store, runners, session_service, progress,
                    bulk_job_id=job_id,
                )
                await store.attach_session_to_bulk_job(job_id, new_id)
                await store.increment_bulk_job(job_id, completed=1)
            except Exception:
                await store.increment_bulk_job(job_id, failed=1)

    await asyncio.gather(*(one(s) for s in source_session_ids))
    await store.set_bulk_job_status(job_id, BulkJobStatus.COMPLETE)
