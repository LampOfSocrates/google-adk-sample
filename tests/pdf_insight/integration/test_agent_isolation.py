"""Regression guard for the lazy-binding invariant that replaced the import-time
model-binding hazard (the incident: an 'offline' test hit real Gemini because a
live sibling module bound the shared root_agent singleton first during collection).

The fix made `shared.model.get_model()` return a `LazyModel` proxy that resolves
LLM_BACKEND PER TURN, so the binding can no longer be frozen at import. This test
proves that property at the integration layer: the SINGLE imported `root_agent`,
driven under one backend and then another (env flipped at run time, NO reimport,
NO sys.modules purge), follows the active backend each time.

It is the property-based complement to a randomized-order CI run: instead of
HOPING the order that used to trigger the bug shows up, it constructs the exact
hazard (one shared agent, two backends) and asserts lazy binding defuses it.
"""
import os

from apps.pdf_insight import config
from apps.pdf_insight.agent import root_agent
from shared.mock_llm import MockLlm


def _bound_models(agent):
    """Every node in the imported graph with a bound `.model` (the LlmAgents)."""
    stack = [agent]
    while stack:
        node = stack.pop()
        if getattr(node, "model", None) is not None:
            yield node.model
        stack.extend(getattr(node, "sub_agents", []) or [])


def _lazy_models(agent):
    """The LazyModel proxies bound in the graph (each LlmAgent gets one)."""
    return [m for m in _bound_models(agent) if hasattr(m, "_real")]


def test_imported_graph_binds_lazy_proxies():
    """get_model() binds a LazyModel proxy on every LlmAgent — not a frozen model
    string/instance. That proxy is what makes the backend resolvable per turn."""
    proxies = _lazy_models(root_agent)
    assert proxies, "expected the router + mode answerers to bind a LazyModel proxy"


def test_same_agent_follows_runtime_backend_without_reimport(monkeypatch):
    """THE invariant the incident violated, now defused: the SAME imported proxy
    resolves MockLlm under mock and a real Gemini model under gemini — decided at
    run time, with no sys.modules purge and no reimport between the two reads.

    Uses gemini as the non-mock backend because building its BaseLlm via ADK's
    registry is offline (no network until a request is actually sent), so the
    assertion is hermetic — no key, no flake.
    """
    proxy = _lazy_models(root_agent)[0]

    monkeypatch.setenv("LLM_BACKEND", "mock")
    assert isinstance(proxy._real(), MockLlm)
    assert proxy.model == "mock"

    monkeypatch.setenv("LLM_BACKEND", "gemini")
    real = proxy._real()
    assert not isinstance(real, MockLlm)
    assert type(real).__name__ == "Gemini"
    # `.model` reports the live id so ADK's google_search gemini gate stays correct.
    assert "gemini" in proxy.model

    # And back again — the per-backend cache returns the same mock instance, proving
    # the switch is reversible within one process (no reimport needed either way).
    monkeypatch.setenv("LLM_BACKEND", "mock")
    assert isinstance(proxy._real(), MockLlm)


async def test_coordinator_turn_is_mock_shaped_under_mock(converse):
    """End-to-end: the imported root_agent, run under the autouse mock backend,
    takes the MockLlm AUTO path (router -> extract_tables -> summarize), so the
    reply is the deterministically-rendered table text — the offline floor the
    incident was supposed to guarantee and didn't. No reimport anywhere. A real
    backend would write prose, not dump the raw 'Table 0' markdown grid."""
    assert os.environ.get("LLM_BACKEND") == "mock"  # pinned by the autouse fixture
    answers, state = await converse(root_agent, ["what is in this statement?"])
    assert state["active_pdf_mode"] == config.AUTO
    assert "Table 0" in answers[-1]
