# Implementation Plan — `text_to_diagram` app

Status: **IMPLEMENTED.** Pipeline + deterministic renderer + mock tests all green.

## Goal
Turn free text into a knowledge graph as mermaid: extract (subject, predicate,
object) triads from prose, then render them as a mermaid `flowchart`. Built as a
two-stage ADK pipeline that contrasts an LLM stage with a deterministic stage.

## ADK principle driving the design
- Triad extraction needs judgment → `LlmAgent`.
- Triads → mermaid is pure string templating → a **deterministic custom
  `BaseAgent`** (zero tokens, deterministic) — still "a different agent" in the
  pipeline, per the requested mental model.

## Pipeline (`SequentialAgent`)
```python
extractor = Agent(
    name="triad_extractor", model=get_model(),
    instruction="Extract every (subject, predicate, object) triad from the text. "
                "Return JSON only, no commentary.",
    output_schema=TriadList,   # pydantic -> ADK forces validated JSON
    output_key="triads",       # writes session.state["triads"]
)

mermaid_builder = MermaidAgent(name="mermaid_builder")  # deterministic BaseAgent

root_agent = SequentialAgent(
    name="text_to_diagram", sub_agents=[extractor, mermaid_builder])
```

### Key ADK facts baked in
- `output_schema` + `output_key` is the canonical hand-off: validated `TriadList`
  JSON lands in `state["triads"]`; the next stage reads state — no prompt-passing,
  no parsing.
- **Constraint:** an `LlmAgent` with `output_schema` set **cannot use tools and
  cannot transfer** (controlled-generation mode). Fine — `extractor` is a pure
  transform.

## Schemas (`shared/schemas.py`)
```python
class Triad(BaseModel):
    subject: str
    predicate: str
    object: str
class TriadList(BaseModel):
    triads: list[Triad]
```

## MermaidAgent (deterministic)
```python
class MermaidAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        data = ctx.session.state.get("triads", {})
        triads = data.get("triads", []) if isinstance(data, dict) else data
        diagram = render_mermaid(triads)
        yield Event(author=self.name, content=text_content(diagram))
```
`render_mermaid(triads)`:
- emit `flowchart LR`
- dedupe nodes; assign stable sanitized IDs (mermaid IDs can't contain spaces /
  punctuation) while keeping the human label: `n0["Paris"]`
- one edge per triad: `n0 -->|capital of| n1`
- wrap output in a ```` ```mermaid ```` fence so `adk web` / markdown renders it.

## Components & files
```
backend/text_to_diagram/
  __init__.py        # from . import agent
  agent.py           # extractor, MermaidAgent, root_agent (SequentialAgent)
  render.py          # render_mermaid (pure; unit-tested with no LLM)
shared/
  schemas.py         # Triad, TriadList
```

## MockLlm additions (`shared/mock_llm.py`)
- When the request carries `response_schema == TriadList` (controlled generation),
  return a small canned triad JSON, e.g.
  `{"triads":[{"subject":"Paris","predicate":"capital of","object":"France"}]}`.
- `MermaidAgent` is deterministic — no mock needed; unit-test `render_mermaid`
  directly.

## Tests (phase-based)
```
tests/diagram/
  test_phase1_render.py   # render_mermaid: dedupe, ID sanitize, edge labels — no LLM
  test_phase2_pipeline.py # full Sequential under mock: text -> triads -> mermaid
  eval_set_1.evalset.json # live (gemini) triad extraction quality
```

## Composition payoff (later phase)
Because `extract_full_text` (pdf_insight) and `extractor` are reachable, a later
`SequentialAgent` can chain **PDF → full text → triads → mermaid**, reusing both.
This is the reason `shared/` exists. Out of scope for first pass.

## Rollout phases
1. `shared/schemas.py` + `render.py` + `render_mermaid` tests (no LLM).
2. `extractor` (`output_schema`/`output_key`) + `MermaidAgent` + `SequentialAgent`.
3. MockLlm triad branch + full-pipeline test under mock.
4. (later) PDF→triads→mermaid composition.

## Open questions / risks
- Mermaid diagram type: `flowchart LR` default — acceptable, or also support
  `graph`/`mindmap` later?
- Triad granularity is model-dependent; eval set will pin expected quality on a
  couple of fixed inputs.
