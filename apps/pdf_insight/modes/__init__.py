"""Registry of PDF mode agents.

Each mode module exposes a `build()` returning {mode_constant: agent}. Adding a
new strategy = adding a module here and listing its `build` in MODE_BUILDERS;
the coordinator picks it up automatically via build_dispatch().
"""
from __future__ import annotations

from . import corpus, pdfbytes, pdfpart, text2sql

# Order is cosmetic; keys are the config mode constants and must be unique.
MODE_BUILDERS = (pdfpart.build, text2sql.build, pdfbytes.build, corpus.build)


def build_dispatch() -> dict:
    """Merge every mode module's build() into one {mode_constant: agent} map.

    Call once per process — each call constructs fresh agent instances, and an
    ADK agent may only be attached to a single parent.
    """
    dispatch: dict = {}
    for build in MODE_BUILDERS:
        for mode, agent in build().items():
            if mode in dispatch:
                raise ValueError(f"Duplicate mode in registry: {mode}")
            dispatch[mode] = agent
    return dispatch
