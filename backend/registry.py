"""App registry + agent (re)builder for the server.

Maps an app name to the module that exposes `root_agent`, and builds a fresh agent
graph for a given backend — purging the package first so backend-gated *structure*
(which tools attach, schema-vs-prompt) rebinds, then overlaying the saved prompt/
model edits. This is the server-side equivalent of the Streamlit app's old
`_get_runner` rebuild step.
"""
from __future__ import annotations

import importlib
import os
import sys

from backend import overrides

# name -> module exposing `root_agent`. Each is a separate ADK app/session.
APPS = {
    "pdf_insight": "backend.pdf_insight.agent",
    "travel_planner": "backend.travel_planner.agent",
    "graph_builder": "backend.graph_builder.agent",
    "text_to_diagram": "backend.text_to_diagram.agent",
}
BACKENDS = ["mock", "gemini", "openai", "deepseek", "bedrock"]
USER_ID = "you"


def build_root_agent(app: str, backend_name: str):
    """Build a fresh `root_agent` for `app` under `backend_name`, with overlays applied.

    Sets LLM_BACKEND and purges the app package so the agent rebuilds with the
    backend-correct structure (the model object itself still binds lazily per turn),
    then applies `data/agent_overrides/<app>.json`.
    """
    if app not in APPS:
        raise KeyError(f"unknown app {app!r}; known: {list(APPS)}")
    os.environ["LLM_BACKEND"] = backend_name
    module_path = APPS[app]
    pkg = module_path.rsplit(".", 1)[0]  # "backend.pdf_insight.agent" -> "backend.pdf_insight"
    for name in [m for m in list(sys.modules) if m == pkg or m.startswith(pkg + ".")]:
        del sys.modules[name]
    module = importlib.import_module(module_path)
    overrides.apply(module.root_agent, overrides.load(app))
    return module.root_agent
