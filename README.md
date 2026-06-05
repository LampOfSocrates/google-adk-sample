# google-adk-sample

A multi-agent ADK sample. Currently: a travel-planning coordinator (`weather_agent/`)
that routes to a weather sub-agent and a web-search agent-tool. Intended to grow into
a small suite of agent-backed apps: **Travel Planning Chat**, **PDF Insight**, and
**Text-to-Diagram**.

Run the agents locally with `adk web` (dev UI + debugger) or `python scripts/chat.py`
(terminal REPL). See `.env.example` for backend selection
(`mock` / `gemini` / `openai` / `deepseek` / `bedrock`).

---

## Testing

```bash
pytest -m "not live"        # fast, offline — runs on MockLlm, no API/quota (default for CI)
pytest                      # everything (live tests hit a real LLM)
pytest -m live              # only the live tests, on Gemini (default backend)
```

**Live tests against a real LLM.** Pick the backend with `--backend` — no env-var
juggling, and it works across the whole suite (the offline tests stay on mock):

```bash
pytest -m live --backend deepseek    # DeepSeek  (needs DEEPSEEK_KEY in .env)
pytest -m live --backend openai      # OpenAI    (needs OPENAI_KEY)
pytest -m live --backend gemini      # Gemini    (needs GOOGLE_API_KEY; this is the default)
```

On Windows PowerShell, invoke pytest via the venv:
`.venv\Scripts\python.exe -m pytest -m live --backend deepseek -v`

How it works: the agent binds its model **at import**, so `conftest.py` freezes the
`--backend` choice (in `LIVE_BACKEND`) before collection, and each live test module
re-applies it just before importing its agent. That's why a live run is immune to the
offline modules that force `LLM_BACKEND=mock` at import. openai/deepseek/bedrock route
through ADK's LiteLlm wrapper — `pip install "google-adk[extensions]"`.

> Note: `test_phase3` (web search) asserts on a `google_search` result, which only runs
> on Gemini; other backends get the offline `web_search` stand-in. Use `test_phase1`/
> `test_phase2` as the clean cross-backend smoke tests.

---

## DecisionLog

A running record of UI/architecture decisions, what they buy us, and what they cost —
so future-us (and new contributors) know *why*, not just *what*. Newest first.

### 2026-06-05 — UI architecture: one FastAPI server, product UIs as siblings of `adk web`

**Decision.** Don't replace `adk web`; **invert it.** `get_fast_api_app(agents_dir=...,
web=True)` returns a plain `FastAPI` app that already mounts the dev UI *and* every debug
endpoint (`/run_sse`, sessions, events, traces, eval, artifacts). We add our own product
routes to that same app, so each use-case gets a bespoke UI **as a sibling** of the dev UI,
sharing the same agents and sessions.

```
agents/                 # one folder per agent — dev UI auto-discovers all
backend/  -> get_fast_api_app(web=True)
  /dev-ui               # full adk web debugger, untouched
  /chat /pdf /diagram   # our product UIs, same /run_sse + session endpoints
```

| Good | Bad / Limits |
|---|---|
| Product UIs and the debugger share **one server, one agent backend, one session store**. | The Angular dev UI is a **sealed bundle** — can't inject child views/tabs *inside* it. We sit beside it, not within it. |
| Because sessions are shared, a "Debug" deep-link opens the dev UI on the **exact session a user just had** — debug real conversations, not a sandbox. | Branding hooks (`logo_text`, `logo_image_url`, `url_prefix`) only *theme* the dev UI; they don't reshape it into a product surface. |
| New agent = new `agents/` folder (auto-discovered) + optional product route. Debug layer is free. | Product panes are still **our code** — `adk web` renders no PDF viewer or diagram for us. |

### 2026-06-05 — Frontend stack: Streamlit (for now)

**Decision.** Build the three product panes in **Streamlit**, mounted alongside the dev UI.
Chosen for speed and staying in Python (same language as the agents). Revisit if/when a
pane needs interactions Streamlit's execution model can't give.

**Structural ceiling (applies to every pane).** Streamlit reruns the whole script top-to-
bottom on each interaction. `st.fragment` (partial reruns) and `st.write_stream` (token
streaming) blunt this, but it's the ceiling — it bites *stateful* surfaces (scroll
position, partial edits, streaming), not stateless ones.

Per use-case:

| Use-case | Good | Bad / Limits | Verdict |
|---|---|---|---|
| **Travel Planning Chat** | Chat fits Streamlit natively; `st.write_stream` gives token streaming from `/run_sse`. | Rerun model needs `st.session_state` to hold history; care needed around streaming + reruns. | ✅ Good fit |
| **PDF Insight** | `streamlit-pdf-viewer` supports page nav **and annotation overlays** — highlight the exact region a citation came from. | Iframe-based community component; **large PDFs (100+ pp) sluggish**; reruns can reset scroll / flicker. | ✅ Good enough; watch big docs |
| **Text-to-Diagram (render-only)** | `st.graphviz_chart()` is **native and clean**; Mermaid via `streamlit-mermaid`. | Static rendered image in an iframe. | ✅ Fine if the diagram is a viewed/downloaded artifact |
| **Text-to-Diagram (editable canvas)** | — | No node dragging / click-to-edit / live re-layout. **Hits the ceiling.** | ❌ Needs React |
| **Plotter** | Streamlit's home turf — `st.plotly_chart` gives full zoom/pan/hover/selection. | None material. | ✅ Excellent |

**Revisit triggers (move a pane to React/Next when):**
- Text-to-Diagram must be an **interactive editor**, not a generated-diagram display.
- PDF Insight needs smooth handling of **large documents** or rich in-document interaction.
- Streaming/stateful flicker from the rerun model becomes a UX blocker users notice.

Net: Streamlit gives a genuinely good UI for the plotter, PDF view, and diagram *render*,
and a solid demo of all three. The only hard miss is an *editable* diagram canvas.
