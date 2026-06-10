"""Integration — guards the import-time model-binding hazard that caused the
incident (an 'offline' test hitting real Gemini because a live sibling module
bound the shared root_agent singleton first during collection).

This is an integration test because it builds and inspects a real ADK agent graph
through `get_model()` — it can't be proven at the unit layer (the failure only
exists because of cross-module import order + a cached singleton). It is the
property-based complement to a randomized-order CI run: instead of HOPING the
order that triggers the bug shows up, it constructs that order deterministically.

Strategy: rebuild the pdf graph under a chosen backend the same way the live
fixture does (purge `apps.pdf_insight*` from sys.modules, then re-import), and
assert the model each LlmAgent bound matches the backend that was active AT
IMPORT — proving binding follows the build-time env, not a leaked singleton.
"""
from shared.mock_llm import MockLlm
from tests.pdf_insight._graph import rebuild_pdf_agent as _rebuild_graph


def _llm_agents(agent):
    """Walk the agent tree, yielding every node that has a bound `.model`."""
    stack = [agent]
    while stack:
        node = stack.pop()
        if getattr(node, "model", None) is not None:
            yield node
        stack.extend(getattr(node, "sub_agents", []) or [])


def test_offline_build_binds_mock_model(monkeypatch):
    """A graph built under LLM_BACKEND=mock binds MockLlm everywhere — the
    invariant the incident violated. If a cached live-bound singleton leaked in,
    some node's model would be a Gemini string instead."""
    root = _rebuild_graph("mock", monkeypatch)
    models = [n.model for n in _llm_agents(root)]
    assert models, "expected at least the router + mode answerers to bind a model"
    assert all(isinstance(m, MockLlm) for m in models), (
        f"offline build leaked a non-mock model: {[type(m).__name__ for m in models]}")


def test_rebuild_after_a_live_build_returns_to_mock(monkeypatch):
    """The exact incident order, deterministically: build the graph 'live'
    (gemini → string model ids) FIRST, then rebuild offline. The offline rebuild
    must bind mock — proving the purge+reimport severs the cached singleton and
    no live binding survives into the offline graph."""
    live = _rebuild_graph("gemini", monkeypatch)
    assert any(isinstance(n.model, str) for n in _llm_agents(live)), (
        "sanity: a gemini build should bind string model ids")

    offline = _rebuild_graph("mock", monkeypatch)
    assert all(isinstance(n.model, MockLlm) for n in _llm_agents(offline)), (
        "offline rebuild after a live build leaked the live binding")


def test_build_is_idempotent_no_parent_conflict(monkeypatch):
    """build_router()/_make_answerer() must return FRESH agents each build (an ADK
    agent attaches to exactly one parent). Two consecutive builds must both
    succeed — a regression here resurfaces the 'already has a parent' error the
    factory functions exist to prevent (see coordinator.build_router docstring)."""
    first = _rebuild_graph("mock", monkeypatch)
    second = _rebuild_graph("mock", monkeypatch)
    assert first is not second
    assert {n.name for n in _llm_agents(first)} == {n.name for n in _llm_agents(second)}
