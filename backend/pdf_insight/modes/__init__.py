"""Registry of PDF mode agents.

Each mode's build() returns {mode_constant: agent}. New strategy = new module
listed in MODE_BUILDERS.
"""
from __future__ import annotations

from . import corpus, pdfbytes, pdfpart, text2sql

# Order is cosmetic; keys must be unique.
MODE_BUILDERS = (pdfpart.build, text2sql.build, pdfbytes.build, corpus.build)


def build_dispatch() -> dict:
    """Merge every build() into one {mode_constant: agent} map.

    Call once per process — each call builds fresh agents, and an ADK agent
    attaches to only one parent.
    """
    dispatch: dict = {}
    for build in MODE_BUILDERS:
        for mode, agent in build().items():
            if mode in dispatch:
                raise ValueError(f"Duplicate mode in registry: {mode}")
            dispatch[mode] = agent
    return dispatch
