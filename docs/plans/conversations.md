# Implementation Plan — Conversations (save / new / autosave)

Status: **PLANNED.** Not yet implemented — this doc is the design.

## Goal
Persist chat sessions to disk so they survive restarts and can be revisited:
- **Save conversation** button → writes the current chat to the backend.
- **New conversation** button → starts a fresh chat (already exists; we formalize it).
- **Load conversation** → reopen a saved one from a list.
- **Autosave** every 5 minutes, the interval configurable in code.
- On-disk layout: **one conversation per folder**.

## What a "conversation" is (today, in `streamlit_app.py`)
The displayable transcript already lives entirely in `st.session_state`:
- `messages` — the chat turns the UI replays: `{role, content, steps, sql_results,
  latency, usage}` per turn. **This is the source of truth for display.**
- `debug_turns` — raw ADK event snapshots per turn (Debug tab). Larger; optional to persist.
- `app`, `backend`, `session_id` — which app/backend produced it; the active PDF
  (`os.environ["PDF_PATH"]`) and `pdf_mode` for pdf_insight.

The ADK `InMemoryRunner` session (the model's own memory) is **not** trivially
serializable and is *not* required to redraw a conversation. So v1 persists the UI
transcript + metadata and treats reopening as **read/replay**, not "resume the LLM's
context." (Resuming model memory is a documented follow-up, below.)

## On-disk layout — one folder per conversation
```
data/conversations/
  20260611-204500_pdf-insight_total-vega/      # <id> = <ts>_<app>_<title-slug>
    meta.json        # id, title, app, backend, created_at, updated_at,
                     # turn_count, pdf_path, pdf_mode
    messages.json    # the `messages` list (the transcript)
    debug_turns.json # optional; only if "save debug data" is on (can be big)
```
- `id` is filesystem-safe and sortable: `YYYYMMDD-HHMMSS_<app>_<slug>`. Stable for
  the life of the conversation (autosave overwrites the same folder).
- `title` defaults to the first user message (slugified, truncated); editable later.
- JSON only — `messages`/`debug_turns` are already plain dicts/lists. The one
  non-JSON risk is a numpy/tuple sneaking in via `sql_results`; serialize with
  `json.dumps(..., default=str)` and coerce rows to lists on save.

## New module — `apps/pages/conversations.py`
Mirrors `agent_overrides.py` (pure persistence + one render helper):
```python
ROOT = os.path.join("data", "conversations")
AUTOSAVE_INTERVAL_SECONDS = 300          # 5 min — the single config knob
SAVE_DEBUG_TURNS = True                  # persist raw snapshots too?

def new_id(app, title) -> str            # "<ts>_<app>_<slug>"
def save(conv_id, *, app, backend, messages, debug_turns, meta_extra) -> str
def load(conv_id) -> dict                # {meta, messages, debug_turns}
def list_conversations() -> list[dict]   # [meta, …] newest first (read meta.json)
def delete(conv_id) -> None
def render_sidebar(ss) -> None           # buttons + load/delete UI (lazy `import streamlit`)
```
Timestamps: `Date.now()`-style calls are fine in app code (this isn't a workflow
script) — use `datetime.now()`.

## UI wiring (`streamlit_app.py`, sidebar)
A "💾 Conversations" section under the existing controls:
- **Save** → `new_id` once per conversation (store `conv_id` in session_state),
  then `conversations.save(...)`; toast the path. Re-Save overwrites the same folder.
- **New** → reuse the existing "New conversation" reset (pop `runner/session_id/
  messages/app/backend/debug_turns`) and clear `conv_id`. Keep the button; just
  also clear `conv_id` and `last_autosave_ts`.
- **Load** → `st.selectbox` over `list_conversations()` (label = title + timestamp).
  On pick: set `messages`/`debug_turns` from `load()`, set `conv_id`, and restore
  `app`/`backend` (which triggers `_get_runner` to rebuild for that app). The model
  starts with empty memory (see follow-up); the transcript redraws fully.
- **Delete** → `delete(conv_id)` + reset.

## Autosave — every 5 min, configurable
Streamlit has no background thread; it reruns on interaction. Two complementary triggers:
1. **Per-turn save** (cheap insurance): after each completed turn, if `conv_id` is
   set, `save(...)`. Costs nothing noticeable and means a crash loses ≤1 turn.
2. **Timed autosave** via a fragment that self-reruns on a timer:
   ```python
   @st.fragment(run_every=conversations.AUTOSAVE_INTERVAL_SECONDS)
   def _autosave_tick():
       ss = st.session_state
       if ss.get("conv_id") and ss.get("messages"):
           conversations.save(ss["conv_id"], app=ss["app"], backend=ss["backend"],
                              messages=ss["messages"], debug_turns=ss["debug_turns"])
   ```
   `st.fragment(run_every=...)` reruns just the fragment on the interval without a
   full-page rerun — the supported way to get periodic work. The interval reads the
   module constant, so changing the cadence is a one-line code edit.
   - Auto-name on first autosave: if `conv_id` is unset but a transcript exists,
     mint one from the first user message so autosave has a folder to write to.
   - Guard with a content hash / `updated_at` so an idle session doesn't rewrite
     identical bytes every tick.

## Edge cases & decisions
- **Mermaid / SQL payloads** in `messages` are plain strings/dicts → JSON-safe with
  `default=str`; coerce `sql_results` rows to `list` on save.
- **App/backend mismatch on load**: restoring `app`/`backend` drives `_get_runner`
  to rebuild the right graph; the agent-overlay JSON still applies on top.
- **Big debug_turns**: gate behind `SAVE_DEBUG_TURNS`; raw events can dwarf the
  transcript. Default on, easy to flip off.
- **Concurrent tabs**: last writer wins per folder (acceptable for a local dev UI).

## Test plan (offline, `LLM_BACKEND=mock`)
- `save` → `load` round-trips `messages`/`meta`; folder layout is one-per-conversation.
- `list_conversations` returns newest-first and tolerates a corrupt `meta.json`.
- `new_id` is filesystem-safe and collision-resistant (timestamp + slug).
- A non-JSON cell in `sql_results` still serializes (no crash).
- Autosave no-ops when the transcript is unchanged (hash guard).

## Follow-ups (out of scope for v1)
- **Resume model memory**: replay saved `messages` into a fresh ADK session (append
  reconstructed `user`/`model` `Content`s to the session service) so a reopened
  conversation continues with context, not just visually.
- Rename/retitle, search, and export (the Debug tab already exports raw events).
- Pluggable backend (SQLite/S3) behind the same `save/load/list` interface.
```
