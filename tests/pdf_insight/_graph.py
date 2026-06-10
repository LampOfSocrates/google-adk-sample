"""Build the pdf_insight agent graph under a chosen backend, immune to import order.

Agents bind their model at import time (`root_agent = PdfInsightAgent(... get_model())`),
and Python caches the package — so the FIRST module to import `apps.pdf_insight`
during collection decides the binding for every test that reuses the singleton
(pytest imports even deselected modules while collecting). Purge the package and
re-import under the backend this test actually wants. Everything goes through
`monkeypatch`, so both the env change and the sys.modules purge auto-revert after
the test and cannot leak into a later one.
"""
import importlib
import sys


def rebuild_pdf_agent(backend, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", backend)
    for name in [m for m in list(sys.modules)
                 if m == "apps.pdf_insight" or m.startswith("apps.pdf_insight.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module("apps.pdf_insight.agent").root_agent
