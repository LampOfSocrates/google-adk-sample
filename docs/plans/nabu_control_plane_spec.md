# Nabu Agent Control Plane — Backend Specification

## 1. Domain glossary

| Term                          | Meaning                                                                                                                                                                                                                            |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NabuApp`                     | A registered agentic product. **Needed** to give variants a stable parent for ownership, governance, and product-level rollups; without it, variants would float and you couldn't ask "everything for product X."                  |
| `NabuAppVariant`              | Immutable versioned agent spec; unit of A/B testing under a NabuApp. **Needed** because comparing "the agent" to "the agent after I tweaked it" requires both to coexist with stable identities; mutating in place destroys A/B history. |
| `NabuAppSession`              | A chat session bound to exactly one variant for its lifetime. **Needed** so every conversation is attributable to exactly one variant — comparison only works if results are unambiguously sourced.                                |
| `NabuMessage`                 | A user/assistant bubble in a session transcript. **Needed** because the chat UI must render finished transcripts in O(messages), not by re-aggregating thousands of ADK events on every read.                                      |
| `NabuTraceEvent`              | A persisted record of one ADK Event (audit/trace). **Needed** because ADK's `SessionService` can't answer cross-session queries ("all sessions where tool X errored") — trace search depends on our own indexed copy.              |
| `BulkJob`                     | A batch that replays N source sessions under one target variant. **Needed** because replaying many golden sessions under a new variant is a long-running async operation requiring progress, cancellation, and partial-failure handling. |
| `Rubric` / `Rating`           | Scoring criteria and scores attached to sessions, turns, or messages. **Needed** because "is variant B better than A" is meaningless without a shared metric — humans and LLM-judges need a scale to agree (or disagree) on.       |
| `Comment` / `Bookmark`        | Free-text annotation and turn-level navigation markers. **Needed** because quantitative scores lose nuance — reviewers need qualitative notes ("the refund tool fired twice") and personal navigation aids ("the turn where it broke"). |
| `Share` / `ShareLink`         | Access grant to another user, or tokenized share link. **Needed** because debugging is collaborative — engineers must send teammates specific sessions without granting broader workspace access.                                  |
| `NabuToolSpec`                | Catalog entry describing a tool a variant may invoke. **Needed** (eventually) for cross-variant reuse, governance over side-effecting tools, and standalone tool testing; **optional for MVP** with a single use case.            |
| `EvaluationCase`              | A golden case promoted from a real session with expected output / assertions. **Needed** because regression testing requires reproducible inputs with expected outputs; ad-hoc chats are too noisy to gate releases on.           |
| `EvaluationSuite`             | A named, runnable collection of EvaluationCases. **Needed** because different products and regression scenarios need different test packs — the suite is the CI hook for agent development.                                       |

## 2. ADK ↔ Nabu name disambiguation

| ADK term                | Nabu equivalent      | Notes                                                                                                                                                                                                                                                              |
| ----------------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `Session`               | `NabuAppSession`     | Same id for both; Nabu adds variant binding, lineage, tags, status. **Needed** because ADK's Session is runtime-only — it lacks the metadata that makes control-plane queries (by variant, tag, lineage) possible.                                                  |
| `Event`                 | `NabuTraceEvent`     | We persist a copy of every ADK Event. **Needed** because ADK events live inside one session and aren't searchable across sessions or after expiry; trace search and error clustering depend on our indexed copy.                                                   |
| `Content` / `Part`      | `NabuMessage`        | Aggregated, role-tagged bubble. **Needed** because the chat UI must render finished transcripts cheaply — re-aggregating hundreds of streaming events per page load is wrong, and ADK has no "message" type to render from.                                       |
| `Tool` / `FunctionTool` | `NabuToolSpec`       | Catalog metadata vs runtime callable. **Needed** only once you want cross-variant reuse, allow-lists for side-effecting tools, or standalone invocation; otherwise skip.                                                                                            |
| `app_name` (argument)   | `NabuAppVariant.id`  | We always set `Runner.app_name = variant_id`. **Needed** because ADK has no notion of variants — encoding variant into `app_name` is what lets one `SessionService` host many variants of the same product without colliding on session ids.                        |
| `Runner`                | (no wrapper)         | Used via `RunnerCache`. **No wrapper needed** because `Runner` is a runtime execution surface; we only need lifecycle (caching), and a wrapper would just shadow ADK's interface.                                                                                   |
| `SessionService`        | (no wrapper)         | Used directly. **No wrapper needed** because we delegate `session.state` storage to ADK entirely; wrapping would force us to mirror ADK's API for no gain.                                                                                                          |

## 3. Why so few ADK wrappers

Of 13 domains, only three wrap ADK primitives: `NabuAppSession`, `NabuTraceEvent`, `NabuMessage`. The other ten have no ADK equivalent.

Wrappers exist for four concrete reasons:

1. **Cross-session queries** — ADK's `SessionService` is keyed by `(app_name, user_id, session_id)`. It cannot answer "all sessions where tool X erred this week" or "all sessions tagged `golden`". Our wrappers are the indexed copies that make these queries possible.
2. **Domain metadata with nowhere to live in ADK** — `variant_id`, `parent_session_id`, `fork_mode`, `tags`, `display_name`, `events_s3_uri`.
3. **Lifecycle independence** — ADK sessions may be GC'd, aged out, or pinned to a backend with retention limits. Transcripts, ratings, and lineage must persist regardless.
4. **Boundary stability** — ADK is pre-1.0. A wrapper gives a single seam to adapt to ADK changes.

Pass-through (intentionally not wrapped): `session.state`, `Content`, `Part`, `LlmAgent`, `Runner`, `Tool` (runtime), `Artifact`.

## 4. Invariants

- A `NabuAppSession` is bound to exactly one `NabuAppVariant` for its lifetime.
- Variants are immutable: `id`, `code_ref`, `config`, `entrypoint` cannot change. Only `display_name` and `archived` are mutable.
- Experimentation = clone the session (`replay_from_start` or `fork_from_now`), never mutate a variant in place.
- `Runner.app_name == NabuAppVariant.id` is the bridge between Nabu and ADK.

## 5. Variant naming

`NabuAppVariant.display_name` is optional on create; the server fills a fallback:

- **From scratch**: `{nabu_app.slug}-v{n}` where n = current variant count + 1.
- **Clone**: `{parent.display_name} (copy)`, auto-suffixed `-2`, `-3` on collision.
- Uniqueness enforced within a `NabuApp`.
- `display_name` is mutable; `id` (UUID) is the stable reference.

## 6. Turn as optional domain

`turn_index` lives on `NabuMessage` and `NabuTraceEvent`. Promote `Turn` to a domain object only when per-turn metrics matter (status, latency, tokens_in/out, cost).

---

## 7. Features & APIs

### 7.1 Manage NabuApps
*I can register a NabuApp, browse all of them, rename or delete one.*

```
POST   /nabu-apps
GET    /nabu-apps
GET    /nabu-apps/{nabu_app_id}
PATCH  /nabu-apps/{nabu_app_id}
DELETE /nabu-apps/{nabu_app_id}
```

### 7.2 Manage NabuAppVariants
*I can create a variant from scratch or by cloning + editing. Display name optional; the server fills a fallback. I can rename later, browse variants, and archive.*

```
POST  /nabu-apps/{nabu_app_id}/variants            body: {display_name?, entrypoint, config, code_ref, notes?}
GET   /nabu-apps/{nabu_app_id}/variants
GET   /nabu-app-variants/{variant_id}
POST  /nabu-app-variants/{variant_id}/clone        body: {display_name?, config_patch}
PATCH /nabu-app-variants/{variant_id}              body: {display_name}
POST  /nabu-app-variants/{variant_id}/archive
```

### 7.3 Chat
*I can start a NabuAppSession and watch the agent stream its response with tool calls visible.*

```
POST   /nabu-app-sessions                          body: {nabu_app_id, variant_id, user_id}
POST   /nabu-app-sessions/{session_id}/turn        body: {user_text}; response: SSE stream
DELETE /nabu-app-sessions/{session_id}
```

### 7.4 Browse & inspect sessions
*I can filter sessions, read transcripts, see full ADK trace events, and view a session's lineage.*

```
GET /nabu-app-sessions                                                # filters: nabu_app_id, variant_id, user_id, status, parent_session_id, tag
GET /nabu-app-sessions/{session_id}
GET /nabu-app-sessions/{session_id}/messages
GET /nabu-app-sessions/{session_id}/trace-events
GET /nabu-app-sessions/{session_id}/turns/{turn_index}/trace-events
GET /nabu-app-sessions/{session_id}/lineage
```

### 7.5 Experiment
*I can clone-and-replay a session under a new variant, fork from the current turn, or align sessions side-by-side.*

```
POST /nabu-app-sessions/{session_id}/replay        body: {target_variant_id, user_id}
POST /nabu-app-sessions/{session_id}/fork          body: {target_variant_id, user_id, copy_state?: bool}
GET  /nabu-app-sessions/compare?ids=a,b,c
```

### 7.6 Bulk evaluate
*I can run one variant against many source sessions, watch progress live, browse jobs, see clones, and cancel.*

```
POST   /bulk-jobs                                  body: {target_variant_id, source_session_ids, user_id}
GET    /bulk-jobs                                  filters: status, target_variant_id
GET    /bulk-jobs/{job_id}
GET    /bulk-jobs/{job_id}/sessions
GET    /bulk-jobs/{job_id}/stream                  SSE progress
DELETE /bulk-jobs/{job_id}                         cancel
```

### 7.7 Rate & score
*I can define rubrics and attach scores to sessions, turns, or messages, and list/update/delete my ratings.*

**Why this feature exists**: variant comparison is only meaningful with comparable metrics. Rubrics define the shared scale; ratings are the data points. Without them, "is the new variant better" is opinion, not data.

```
POST  /rubrics                  GET    /rubrics                    GET /rubrics/{rubric_id}
POST  /ratings                  GET    /ratings?target_type=&target_id=&rubric_id=
GET   /ratings/{rating_id}      PATCH  /ratings/{rating_id}        DELETE /ratings/{rating_id}
```

### 7.8 Comment & annotate
*I can leave notes on a session, turn, or message; bookmark specific turns.*

**Why this feature exists**: scores compress nuance. Reviewers need qualitative notes ("the refund tool fired twice with the wrong arg") that ratings can't carry. This is what makes the session browser useful for collaborative debugging instead of just data archaeology.

```
POST   /comments                GET    /comments?target_type=&target_id=
GET    /comments/{comment_id}   PATCH  /comments/{comment_id}      DELETE /comments/{comment_id}
```

### 7.9 Share
*I can grant a teammate access to a session or create a tokenized share link, see who has access, and revoke either.*

**Why this feature exists**: debugging is collaborative. Engineers ping each other with "look at this session" constantly; without explicit shares or share links, the only fallback is granting broad workspace access, which doesn't scale.

```
POST   /nabu-app-sessions/{session_id}/shares
GET    /nabu-app-sessions/{session_id}/shares
DELETE /shares/{share_id}
POST   /nabu-app-sessions/{session_id}/share-links
DELETE /share-links/{token}
```

### 7.10 Aggregate metrics
*I can see rolled-up scores, latency, and token usage across a bulk job; compare a job to its source parents or against another bulk job.*

**Why this feature exists**: individual session ratings are too noisy to decide on a release. Rolled-up statistics (mean, p95, win-rate vs parent) are the "did the new variant win" view — without them, release decisions are anecdote-driven.

```
GET /bulk-jobs/{job_id}/metrics
GET /bulk-jobs/{job_id}/compare-to-parents
GET /bulk-jobs/compare?ids=a,b
```

### 7.11 Live session debugging
*I can pause, resume, or abort an in-flight turn; read and edit live state; step one event at a time; inject synthetic tool results or LLM responses; tail events live.*

**Why this feature exists**: when a production turn misbehaves, engineers must pause execution, inspect/edit agent state, override tool results, and step through events without throwing the session away. Without these, debugging stuck agents means restarting the conversation — losing the context that caused the bug.

> ⚠ Gated behind a `debug_mode` flag on the `NabuAppSession`, off by default.

```
POST  /nabu-app-sessions/{session_id}/pause
POST  /nabu-app-sessions/{session_id}/resume
POST  /nabu-app-sessions/{session_id}/abort
GET   /nabu-app-sessions/{session_id}/state
PATCH /nabu-app-sessions/{session_id}/state
POST  /nabu-app-sessions/{session_id}/step
POST  /nabu-app-sessions/{session_id}/inject-tool-result          body: {tool_call_id, result}
POST  /nabu-app-sessions/{session_id}/inject-llm-response         body: {content}
GET   /nabu-app-sessions/{session_id}/stream                      live event tail (SSE)
```

### 7.12 Offline replay surgery
*I can replay a single turn, replay with an edited user message or swapped tool result, replay under transient variant overrides, and invoke a tool standalone.*

**Why this feature exists**: full-session replay is too coarse for diagnosis. The real loop is "re-run *this* turn with a tweaked prompt" or "what if this tool had returned X?" Single-turn replay and overrides isolate root causes without re-running entire conversations.

```
POST /nabu-app-sessions/{session_id}/turns/{turn_index}/replay
POST /nabu-app-sessions/{session_id}/turns/{turn_index}/replay-with-edit   body: {user_text?, tool_overrides?}
POST /nabu-app-sessions/{session_id}/replay-with-overrides                 body: {config_patch}
POST /nabu-tool-specs/{tool_name}/invoke                                   body: {args}
```

### 7.13 Trace search & error grouping
*I can search across sessions; list sessions where a tool errored; see clustered error groups; pull an OpenTelemetry-style trace for one turn.*

**Why this feature exists**: with thousands of sessions, "find the bad ones" is the bottleneck. Free-text search, structured event filters, and automatic clustering of failures by fingerprint turn a manual scrub into a query.

```
GET /search/messages?q=&nabu_app_id=&variant_id=
GET /search/trace-events?tool=&author=&min_latency_ms=&error_type=
GET /errors
GET /errors/{fingerprint}/sessions
GET /nabu-app-sessions/{session_id}/turns/{turn_index}/trace
```

### 7.14 Tagging & triage
*I can tag sessions, bookmark specific turns, and filter the session browser by tag.*

**Why this feature exists**: ratings are quantitative; tags are ad-hoc qualitative (`regression`, `golden`, `customer-reported`). Bookmarks pin specific turns. Both are essential for review workflows that span days and people — the system needs to remember what's "interesting" between visits.

```
POST   /nabu-app-sessions/{session_id}/tags        body: {tag}
DELETE /nabu-app-sessions/{session_id}/tags/{tag}
POST   /bookmarks                  GET /bookmarks               DELETE /bookmarks/{bookmark_id}
```

### 7.15 NabuToolSpec registry
*I can register a tool that variants may invoke, list and inspect specs, update its allow-list, and remove specs.*

**Why this feature exists**: only required when multiple variants/apps share tools, when tools have side effects that need allow-lists, or when standalone tool-isolation testing is part of UX. **For a single-use-case MVP, defer this** — variants can just import tool callables directly from `<usecase>/tools/`.

```
POST   /nabu-tool-specs                            body: {name, owner, entrypoint, json_schema, allowed_variant_ids?}
GET    /nabu-tool-specs
GET    /nabu-tool-specs/{name}
PATCH  /nabu-tool-specs/{name}                     body: {allowed_variant_ids?}
DELETE /nabu-tool-specs/{name}
```

### 7.16 Evaluation suites
*I can promote a session (or one of its turns) into an EvaluationCase, group cases into an EvaluationSuite, run a suite against any variant, and schedule a suite to run on every new variant of a NabuApp.*

**Why this feature exists**: golden sessions are wasted if they sit in tags. Promoting them into named suites, running suites against every new variant, and producing pass/fail diffs is the CI hook for agent development. Without it, there's no automated regression gate — quality is whatever the last reviewer caught.

```
POST   /evaluation-cases               GET /evaluation-cases               DELETE /evaluation-cases/{case_id}
POST   /evaluation-suites              GET /evaluation-suites              GET    /evaluation-suites/{suite_id}
POST   /evaluation-suites/{suite_id}/cases
DELETE /evaluation-suites/{suite_id}/cases/{case_id}
POST   /evaluation-suites/{suite_id}/runs?variant_id=
GET    /evaluation-suite-runs/{run_id}
POST   /nabu-apps/{nabu_app_id}/auto-eval-policy   body: {suite_ids, trigger: "on_variant_create"}
```

---

## 8. Repository layout

```
service/
├── src/
│   ├── domain/           Pydantic BaseModels (Nabu's own types)
│   ├── stores/           control-plane persistence (Postgres, S3 archive, in-memory)
│   ├── adk/              RunnerCache, app_name helper, SessionService factory
│   ├── ops/              business logic per feature (chat, replay, fork, debug, …)
│   ├── api/              FastAPI routers + main.py
│   ├── progress/         ProgressSink protocol + Redis/SSE/null impls
│   ├── config.py
│   └── <usecase>/        e.g. support_bot/
│       ├── subagents/    single-purpose ADK agents (building blocks)
│       ├── tools/        FunctionTool callables (own their data access)
│       ├── schemas.py    pydantic types specific to this use case
│       └── v*.py         variant entrypoints (build_agent(config) -> Agent)
├── tests/                see § 9
├── scripts/nabu.py       CLI
├── alembic/              DB migrations
└── pyproject.toml
```

Frontend (Streamlit):

```
apps/
├── streamlit_app.py      st.navigation() entry
└── pages/
    └── <usecase>/
        ├── page.py
        ├── components.py
        └── handlers/     mixin classes, one per API slice + client.py composes them
```

---

## 9. Test layout

Three tiers — **unit** (pure functions, mocked deps), **integration** (real DB / ADK / Redis via testcontainers and moto), **e2e** (full FastAPI app in-process via `httpx.AsyncClient`).

```
service/tests/
├── conftest.py                                 shared fixtures: in-memory store, fake adk, http client
├── fixtures/
│   ├── dummy_agent.py
│   └── dummy_tools.py
│
├── unit/
│   ├── domain/
│   │   └── test_models.py                      pydantic validation, display_name fallback
│   ├── adk/
│   │   ├── test_runner_cache.py                caching, lazy build, agent factory resolution
│   │   └── test_naming.py                      adk_app_name helper
│   ├── stores/
│   │   └── test_in_memory_store.py
│   ├── progress/
│   │   └── test_null_sink.py
│   └── ops/
│       ├── test_chat.py                        (7.3) run_turn yields events, persists messages
│       ├── test_replay.py                      (7.5, 7.6) clean state, progress sink wiring
│       ├── test_fork.py                        (7.5) fork_session_now, copy_state on/off
│       ├── test_turns.py                       (7.12) per-turn replay surgery
│       ├── test_debug.py                       (7.11) pause/resume/inject semantics
│       ├── test_search.py                      (7.13)
│       ├── test_ratings.py                     (7.7) rubric validation, score parsing
│       ├── test_comments.py                    (7.8)
│       ├── test_shares.py                      (7.9)
│       ├── test_tagging.py                     (7.14)
│       └── test_evaluation.py                  (7.16) suite runs, pass/fail diff
│
├── integration/
│   ├── stores/
│   │   ├── test_postgres_store.py              testcontainers Postgres
│   │   └── test_s3_archive_store.py            moto for S3
│   ├── adk/
│   │   ├── test_real_runner.py                 dummy_agent against InMemorySessionService
│   │   └── test_database_session_service.py    ADK DatabaseSessionService persistence
│   ├── ops/
│   │   ├── test_chat_persists.py               run_turn writes durable rows
│   │   ├── test_replay_persists.py             lineage chain correctness
│   │   └── test_bulk_worker.py                 concurrent replays, semaphore, partial failure
│   └── progress/
│       └── test_redis_sink.py                  real Redis pub/sub
│
└── e2e/
    ├── api/
    │   ├── test_apps_api.py                    (7.1)
    │   ├── test_variants_api.py                (7.2) create, clone, rename, archive, display_name fallback
    │   ├── test_sessions_api.py                (7.3, 7.4) chat SSE, list filters, lineage
    │   ├── test_experiment_api.py              (7.5) replay, fork, compare
    │   ├── test_bulk_api.py                    (7.6) submit, watch SSE, cancel
    │   ├── test_ratings_api.py                 (7.7)
    │   ├── test_comments_api.py                (7.8)
    │   ├── test_shares_api.py                  (7.9) grants + tokenized links
    │   ├── test_aggregates_api.py              (7.10)
    │   ├── test_debug_api.py                   (7.11) debug_mode gating, inject paths
    │   ├── test_turns_api.py                   (7.12)
    │   ├── test_search_api.py                  (7.13)
    │   ├── test_tagging_api.py                 (7.14)
    │   ├── test_tools_api.py                   (7.15) skip while deferred
    │   └── test_evaluation_api.py              (7.16)
    ├── cli/
    │   └── test_nabu_cli.py                    exercises scripts/nabu.py against live server
    └── flows/
        ├── test_full_chat_flow.py              app → variant → session → chat → messages
        ├── test_clone_replay_flow.py           parent → replay → compare aggregates
        ├── test_bulk_eval_flow.py              bulk → ratings → aggregate metrics
        ├── test_fork_continue_flow.py          fork-from-now retains state across variants
        └── test_debug_flow.py                  pause → state-patch → inject-llm → resume
```

Frontend tests (Streamlit):

```
apps/tests/
├── unit/handlers/
│   ├── test_apps_mixin.py                      httpx mock; assert path/body
│   ├── test_sessions_mixin.py                  SSE iterator decoding
│   ├── test_debug_mixin.py
│   └── test_client_composition.py              MRO order, ApiBase.__init__ wires args
└── e2e/
    └── test_support_bot_page.py                streamlit.testing.AppTest smoke
```

E2E tests use `httpx.AsyncClient(app=app)` for in-process speed; boot a real server only for `test_nabu_cli.py`. Integration tier uses `testcontainers` for Postgres, `moto` for S3, real Redis in CI.

---

## 10. Summary

- **16 feature areas**, ~75 endpoints, 13 first-class domains.
- **3 ADK wrappers**: `NabuAppSession`, `NabuTraceEvent`, `NabuMessage`. Everything else is net-new.
- **Build order**: 7.1–7.6 MVP. 7.7–7.10 evaluation rigor. 7.11–7.16 debuggability and CI.
