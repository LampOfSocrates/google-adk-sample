"""graph_builder: a *conversational* knowledge-graph builder.

This is text_to_diagram grown up. Where text_to_diagram is stateless (extract
triads from one text, render, forget), graph_builder ACCRETES: a persistent graph
lives in `session.state["graph"]` and every turn folds new text into it. The hard
part — the moat — is stage 2, the resolver: deciding whether a freshly-named
entity is an EXISTING node under a different surface name, or a new one.

Three stages (a stateful SequentialAgent):

  1 extractor  LlmAgent -> state["mentions"]. Pulls entities + relations from this
               turn's text, as WRITTEN (surface names, no resolution).

  2 resolver   LlmAgent -> state["resolutions"]. THE MOAT. Its instruction is a
               *callable* that injects the current graph's nodes + this turn's
               mentions, so it resolves each mention against what already exists:
               attach-to-node-X vs new, with confidence + reason (explainable,
               overridable).

Structured output is backend-conditional (see shared.supports_output_schema): on
native backends (gemini/mock) the two LlmAgents bind ADK's canonical `output_schema`
and state holds a validated object; on LiteLLM providers (openai/deepseek/bedrock),
which reject strict schemas, they instead ask for JSON in the prompt and state holds
a raw string. `_as_obj` reads either form, so stages 2 and 3 are path-agnostic.

  3 grapher    BaseAgent (deterministic). Applies the resolutions to the
               persistent graph — merges aliases, accretes claims WITH provenance
               (source + turn), adds edges — then renders the whole graph and a
               resolution log. Persists via EventActions(state_delta).

Grounding is deliberately FAKED here (chat-only): every claim is soft, carrying
provenance but no verified anchor. This isolates the resolution problem. On the
`mock` backend the resolver can't actually reason, so it returns all-new — which
demonstrates wrong-split and is exactly why the real test needs LLM_BACKEND=gemini.
"""
from __future__ import annotations

import json
import re
from typing import AsyncGenerator

from backend.shared.model import get_model, supports_output_schema
from backend.shared.schemas import MentionList, ResolutionList

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event, EventActions
from google.genai import types

from .render import render_graph

# Native backends (gemini/mock) take ADK's canonical output_schema path; LiteLLM
# providers (openai/deepseek/bedrock) reject strict schemas, so they fall back to
# prompt-for-JSON + _loads. Same instructions/keys either way — only the transport
# differs, so downstream code stays identical via _as_obj().
_STRUCTURED = supports_output_schema()


def _loads(text: str, default):
    """Parse a model's JSON reply, tolerating ```json fences and surrounding prose."""
    if not text:
        return default
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001 - fall back to grabbing the first {...} block
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return default
        return default


def _as_obj(val, default):
    """Read a stage's output regardless of path: a validated dict/model (canonical
    output_schema) or a raw JSON string (prompt+parse fallback)."""
    if val is None:
        return default
    if hasattr(val, "model_dump"):
        return val.model_dump()
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        return _loads(val, default)
    return default


# --- stage 1: extract surface mentions from this turn ---------------------
extractor = LlmAgent(
    name="mention_extractor",
    model=get_model(),
    description="Extracts entities + relations from one turn as written (no resolution).",
    instruction=(
        "Extract the system components (services, datastores, queues, jobs, external "
        "systems) and the relationships between them from the user's text. Use the "
        "names EXACTLY as written — do NOT canonicalize or merge; that happens later. "
        "If the message begins with a [source] tag, ignore the tag itself. For each "
        "entity, capture the facts the text asserts about it as separate claims.\n"
        "Return ONLY a JSON object (no prose, no markdown fences) of this shape:\n"
        '{"entities": [{"name": "<as written>", "kind": '
        '"service|datastore|queue|job|external|unknown", "claims": ["<fact>", ...]}], '
        '"relations": [{"source_name": "<as written>", "predicate": '
        '"calls|depends_on|reads_from|writes_to", "target_name": "<as written>"}]}\n'
        "Always include both keys, even if a list is empty."
    ),
    output_key="mentions",
    **({"output_schema": MentionList} if _STRUCTURED else {}),
)


# --- stage 2: THE MOAT — resolve mentions against the existing graph -------
def _resolver_instruction(ctx: ReadonlyContext) -> str:
    """Inject the current graph + this turn's mentions so the resolver decides
    attach-vs-new against real prior state (the only disambiguator we have, since
    grounding is faked)."""
    graph = ctx.state.get("graph") or {"nodes": {}}
    nodes = graph.get("nodes", {})
    if nodes:
        rows = []
        for nid, n in nodes.items():
            aliases = ", ".join(a for a in n.get("aliases", []) if a) or "none"
            rows.append(
                f'  {nid}: "{n.get("canonical_name")}" '
                f'(kind={n.get("kind", "unknown")}; aliases: {aliases})'
            )
        existing = "EXISTING NODES:\n" + "\n".join(rows)
    else:
        existing = "EXISTING NODES: (none yet — first turn)"

    entities = _as_obj(ctx.state.get("mentions"), {"entities": []}).get("entities", [])
    if entities:
        rows = [f'  - "{e["name"]}" (kind={e.get("kind", "unknown")})' for e in entities]
        to_resolve = "ENTITIES TO RESOLVE (this turn):\n" + "\n".join(rows)
    else:
        to_resolve = "ENTITIES TO RESOLVE: (none)"

    return (
        "You are the entity RESOLVER for a system-architecture knowledge graph.\n"
        "For EACH entity to resolve, decide whether it denotes one of the EXISTING "
        "nodes (decision='attach', set node_id to that id) or a genuinely new "
        "component (decision='new', node_id=null).\n"
        "Components are routinely named differently across sources: 'auth-service', "
        "'auth', 'login backend', 'AuthN', 'Identity Provider' may all be ONE node. "
        "Merge those. But do NOT over-merge distinct things: e.g. 'user-service' "
        "(backend) vs 'user-facing API' (gateway) are different nodes.\n"
        "When attaching, set canonical_name to the EXISTING node's name. When new, "
        "propose a clean canonical_name. Always give a 0-1 confidence and a one-line "
        "reason citing the evidence for your decision.\n\n"
        f"{existing}\n\n{to_resolve}\n\n"
        "Return ONLY a JSON object (no prose, no markdown fences) of this shape, with "
        "exactly one resolution per entity to resolve:\n"
        '{"resolutions": [{"mention_name": "<echo>", "decision": "attach|new", '
        '"node_id": "<existing id or null>", "canonical_name": "<name>", '
        '"confidence": 0.0, "reason": "<one line>"}]}'
    )


resolver = LlmAgent(
    name="entity_resolver",
    model=get_model(),
    description="Resolves each mention to an existing node or a new one (the moat).",
    instruction=_resolver_instruction,
    output_key="resolutions",
    **({"output_schema": ResolutionList} if _STRUCTURED else {}),
)


# --- stage 3: apply resolutions to the persistent graph (deterministic) ----
def _latest_user_text(ctx: InvocationContext) -> str:
    uc = getattr(ctx, "user_content", None)
    if uc and getattr(uc, "parts", None):
        for p in uc.parts:
            if getattr(p, "text", None):
                return p.text
    for ev in reversed(getattr(ctx.session, "events", []) or []):
        if getattr(ev, "author", None) == "user" and getattr(ev, "content", None):
            for p in ev.content.parts or []:
                if getattr(p, "text", None):
                    return p.text
    return ""


def _source_of(text: str) -> str | None:
    """A leading [tag] marks the provenance of this turn's text (e.g. '[wiki] ...')."""
    m = re.match(r"\s*\[([^\]]+)\]", text or "")
    return m.group(1).strip() if m else None


class GrapherAgent(BaseAgent):
    """Folds this turn's resolved mentions into the persistent graph, with
    provenance, then renders the graph + a resolution log."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        graph = state.get("graph") or {"nodes": {}, "edges": [], "turn": 0}
        # copy so the state_delta is a clean replacement, not an in-place alias
        graph = {
            "nodes": dict(graph.get("nodes", {})),
            "edges": list(graph.get("edges", [])),
            "turn": graph.get("turn", 0),
        }
        turn = graph["turn"] + 1
        source = _source_of(_latest_user_text(ctx)) or f"turn{turn}"

        mentions = _as_obj(state.get("mentions"), {"entities": [], "relations": []})
        entities = mentions.get("entities", [])
        relations = mentions.get("relations", [])
        resolutions = _as_obj(state.get("resolutions"), {"resolutions": []}).get(
            "resolutions", []
        )

        claims_by_name = {e["name"]: e.get("claims", []) for e in entities}
        kind_by_name = {e["name"]: e.get("kind", "unknown") for e in entities}

        name_to_id: dict[str, str] = {}
        log: list[str] = []

        def _new_node(name: str, canonical: str, kind: str) -> str:
            nid = f"n{len(graph['nodes'])}"
            graph["nodes"][nid] = {
                "canonical_name": canonical or name,
                "kind": kind,
                "aliases": [name] if name != (canonical or name) else [],
                "claims": [],
            }
            return nid

        for r in resolutions:
            nm = r.get("mention_name", "")
            conf = float(r.get("confidence", 0.0) or 0.0)
            if r.get("decision") == "attach" and r.get("node_id") in graph["nodes"]:
                nid = r["node_id"]
                node = graph["nodes"][nid]
                if nm and nm != node["canonical_name"] and nm not in node["aliases"]:
                    node["aliases"] = [*node["aliases"], nm]
                log.append(
                    f"attach '{nm}' → {nid} ({node['canonical_name']}) "
                    f"[{conf:.2f}] {r.get('reason', '')}"
                )
            else:
                nid = _new_node(nm, r.get("canonical_name", nm), kind_by_name.get(nm, "unknown"))
                log.append(
                    f"new    '{nm}' → {nid} ({graph['nodes'][nid]['canonical_name']}) "
                    f"[{conf:.2f}] {r.get('reason', '')}"
                )
            name_to_id[nm] = nid

        # entities the resolver dropped still land (fallback: new node)
        for e in entities:
            if e["name"] not in name_to_id:
                nid = _new_node(e["name"], e["name"], e.get("kind", "unknown"))
                name_to_id[e["name"]] = nid
                log.append(f"new    '{e['name']}' → {nid} (fallback: no resolution)")

        # accrete claims with provenance
        for nm, nid in name_to_id.items():
            for c in claims_by_name.get(nm, []):
                graph["nodes"][nid]["claims"].append(
                    {"text": c, "source": source, "turn": turn}
                )

        # edges (both endpoints must have resolved this turn or earlier-by-name)
        for rel in relations:
            s = name_to_id.get(rel.get("source_name"))
            t = name_to_id.get(rel.get("target_name"))
            if s and t:
                graph["edges"].append(
                    {
                        "from": s,
                        "to": t,
                        "predicate": rel.get("predicate", "rel"),
                        "source": source,
                        "turn": turn,
                    }
                )

        graph["turn"] = turn

        diagram = render_graph(graph)
        log_text = "\n".join(f"- {line}" for line in log) or "- (nothing extracted)"
        report = (
            f"{diagram}\n\n"
            f"**Resolution log — turn {turn}, source: `{source}`**\n{log_text}\n\n"
            f"_graph now: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges_"
        )

        yield Event(
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=report)]),
            actions=EventActions(state_delta={"graph": graph}),
        )


grapher = GrapherAgent(name="grapher")


root_agent = SequentialAgent(
    name="graph_builder",
    description="Conversationally accretes text into a persistent, resolved knowledge graph.",
    sub_agents=[extractor, resolver, grapher],
)
