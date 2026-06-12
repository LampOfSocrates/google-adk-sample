"""Deterministic renderer for the accumulated graph. No ADK, no LLM.

Unlike text_to_diagram's render (which dedupes by exact label and throws the
graph away each turn), this renders the *persistent* graph the resolver has been
accreting into `session.state["graph"]`: nodes carry aliases + dated claims, so
the label shows how much one canonical node has absorbed across turns/sources.
"""
from __future__ import annotations

import re


def _label(text: str) -> str:
    """mermaid node/edge text can't contain | or "; collapse them out."""
    return re.sub(r'[|"]', " ", str(text)).strip() or "?"


def render_graph(graph: dict, direction: str = "LR") -> str:
    """Render the accumulated graph as a fenced mermaid flowchart.

    Node label = canonical name + (#aliases merged, #claims accreted), so a node
    that correctly absorbed many surface names shows it. Datastores get a second
    shape so service-vs-store is visible at a glance.
    """
    nodes = (graph or {}).get("nodes", {})
    edges = (graph or {}).get("edges", [])
    if not nodes:
        return "```mermaid\nflowchart " + direction + "\n%% (empty graph)\n```"

    decls = []
    for nid, n in nodes.items():
        name = _label(n.get("canonical_name", nid))
        aliases = [a for a in n.get("aliases", []) if a]
        claims = n.get("claims", [])
        tag = f"{name}<br/><small>{len(aliases)} aliases · {len(claims)} claims</small>"
        if n.get("kind") == "datastore":
            decls.append(f'    {nid}[("{tag}")]')  # cylinder for stores
        else:
            decls.append(f'    {nid}["{tag}"]')

    edge_lines = []
    for e in edges:
        src, dst = e.get("from"), e.get("to")
        if src in nodes and dst in nodes:
            edge_lines.append(f'    {src} -->|{_label(e.get("predicate", "rel"))}| {dst}')

    body = "\n".join([f"flowchart {direction}", *decls, *edge_lines])
    return f"```mermaid\n{body}\n```"
