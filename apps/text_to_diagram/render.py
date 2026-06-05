"""Deterministic triad -> mermaid renderer. Pure function, no ADK, no LLM.

ADK idiom note: turning {subject, predicate, object} triads into mermaid is pure
string templating, so it is a *deterministic function* (wrapped by a custom
BaseAgent in agent.py) — NOT an LlmAgent. Putting a model here would only add
cost and nondeterminism for a mechanical transform. Keep this LLM-free.
"""
from __future__ import annotations

import re


def _normalize(triads) -> list[dict]:
    """Accept the several shapes `state['triads']` can take and return a plain
    list of {subject, predicate, object} dicts.

    ADK's output_key may store the controlled-generation result as the TriadList
    dict ({"triads": [...]}), a bare list, or pydantic objects — normalize all.
    """
    if triads is None:
        return []
    if isinstance(triads, dict):
        triads = triads.get("triads", [])
    out = []
    for t in triads:
        if hasattr(t, "model_dump"):
            t = t.model_dump()
        if isinstance(t, dict) and {"subject", "predicate", "object"} <= t.keys():
            out.append({k: str(t[k]) for k in ("subject", "predicate", "object")})
    return out


def _edge_label(text: str) -> str:
    """mermaid edge labels can't contain | or "; collapse them out."""
    return re.sub(r'[|"]', " ", text).strip() or "rel"


def render_mermaid(triads, direction: str = "LR") -> str:
    """Render triads as a fenced mermaid flowchart.

    Nodes are de-duplicated by label and given stable ids (n0, n1, ...); each
    triad becomes one labeled edge `n0 -->|predicate| n1`. Output is wrapped in a
    ```mermaid fence so adk web / markdown renders the diagram.
    """
    items = _normalize(triads)
    if not items:
        return "```mermaid\nflowchart " + direction + "\n%% (no triads)\n```"

    ids: dict[str, str] = {}

    def node_id(label: str) -> str:
        if label not in ids:
            nid = f"n{len(ids)}"
            ids[label] = nid
        return ids[label]

    edges = []
    for t in items:
        src = node_id(t["subject"])
        dst = node_id(t["object"])
        edges.append(f'    {src} -->|{_edge_label(t["predicate"])}| {dst}')

    # Declare nodes first (id["label"]) so labels render verbatim, then edges.
    decls = [f'    {nid}["{label}"]' for label, nid in ids.items()]
    body = "\n".join([f"flowchart {direction}", *decls, *edges])
    return f"```mermaid\n{body}\n```"
