"""The model catalog — which concrete models each provider can use.

One place to see the options per provider and to change the default. `get_model()`
in model.py always picks the FIRST entry for a provider, unless that provider's
`*_MODEL` env var overrides it. The extra entries are documented alternatives:
to switch, set the env var (e.g. DEEPSEEK_MODEL) or move one to the front.

Prices/quality notes are relative to each provider's default and approximate —
they're here to guide the choice, not as a billing source of truth.
"""
from enum import Enum


class Provider(str, Enum):
    """The LLM providers that resolve to a concrete model name.

    'mock' is intentionally absent — MockLlm is offline and takes no model name.
    """

    GEMINI = "gemini"
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    BEDROCK = "bedrock"


# First entry per provider = the default we pick. Trailing entries are
# alternatives, with a note on how they compare to that default.
MODEL_CHOICES: dict[Provider, list[str]] = {
    Provider.GEMINI: [
        "gemini-2.5-flash-lite",  # default: cheapest, largest free-tier daily quota; ample for tool-calling
        "gemini-2.5-flash",       # smarter, but smaller free quota and more $/token
        "gemini-2.5-pro",         # most capable; slowest + priciest — overkill for these samples
    ],
    Provider.OPENAI: [
        "openai/gpt-4o-mini",     # default: cheap, fast, solid tool use
        "openai/gpt-4o",          # stronger general reasoning, ~15-20x the price
        "openai/o4-mini",         # reasoning model: better at multi-step, higher latency + cost
    ],
    Provider.DEEPSEEK: [
        # V4 line. The old deepseek-chat / deepseek-reasoner names are deprecated
        # (sunset 2026-07-24) and now just alias v4-flash's non-thinking/thinking modes.
        "deepseek/deepseek-v4-flash",  # default: cheap+fast ($0.14/$0.28 per Mtok), 1M context, tool calls
        "deepseek/deepseek-v4-pro",    # most capable (reasoning/coding/agents); ~3x flash's price, slower
    ],
    Provider.BEDROCK: [
        # IDs are cross-region INFERENCE PROFILES (the `us.` prefix is required —
        # these models are not offered on-demand). Set per account/region; list
        # yours with: boto3 bedrock.list_inference_profiles().
        "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",   # default: cheap, fast, tool-capable
        "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # stronger reasoning; pricier + slower
        "bedrock/us.anthropic.claude-opus-4-5-20251101-v1:0",    # most capable; much pricier + slower
    ],
}


def default_model(provider: Provider) -> str:
    """The model we pick for `provider` unless its `*_MODEL` env var overrides it."""
    return MODEL_CHOICES[provider][0]
