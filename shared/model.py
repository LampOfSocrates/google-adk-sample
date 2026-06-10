"""One place to choose the model backend for every agent in this project.

Controlled by the LLM_BACKEND env var (see .env):

    mock     (default) -> MockLlm: free, offline, no tokens. Build all day.
    gemini             -> Gemini via API key (GOOGLE_API_KEY). Costs Gemini quota.
    openai             -> OpenAI via LiteLLM (OPENAI_API_KEY). Needs extensions.
    deepseek           -> DeepSeek via LiteLLM (DEEPSEEK_API_KEY). Needs extensions.
    bedrock            -> AWS Bedrock via LiteLLM. Costs AWS. Needs:
                            pip install "google-adk[extensions]"
                            AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION
                            (and the chosen model enabled in the Bedrock console)

openai / deepseek / bedrock all route through ADK's LiteLlm wrapper, which needs
`pip install "google-adk[extensions]"`.

`get_model()` returns a `LazyModel` — a `BaseLlm` proxy that resolves the ACTIVE
backend per turn (not at import). The first turn under a given backend builds and
caches the real underlying model (MockLlm, the Gemini BaseLlm from ADK's registry,
or a LiteLlm); later turns reuse it. This means a single imported `root_agent`
produces mock output under LLM_BACKEND=mock and live output under LLM_BACKEND=gemini
— decided when a turn runs, so import order can never freeze the backend.

The proxy's `.model` attribute is dynamic: it reports the active backend's real
model id (e.g. the Gemini id under gemini). ADK reads `agent.canonical_model.model`
at request-build time to gate model-native tools — notably `google_search`, which
asserts the id `is_gemini_model(...)`. Reporting the real id keeps that intact.

`backend()` / `is_mock()` / `is_gemini()` let app code branch on the active
backend WITHOUT re-reading the env var everywhere. Gate Gemini-only built-in
tools (google_search, native PDF upload) on `is_gemini()` — NOT on `not is_mock()`,
since openai/deepseek/bedrock are also non-Gemini and can't run those tools. These
are read at agent-BUILD time to choose schema-vs-prompt and which tools to attach;
that is structural and legitimately import-time. Only the model OBJECT is lazy.
"""
import os
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from .model_choices import Provider, default_model


def backend() -> str:
    """The active backend name, lower-cased. 'mock' when unset."""
    return os.environ.get("LLM_BACKEND", "mock").strip().lower()


def is_mock() -> bool:
    """True when running on the offline MockLlm (no network, no tokens)."""
    return backend() == "mock"


def is_gemini() -> bool:
    """True only on the native Gemini backend.

    Gemini-only built-in tools (google_search, native PDF upload) work only here.
    Every other backend — mock, openai, deepseek, bedrock — must use an offline
    or function-tool stand-in instead.
    """
    return backend() == "gemini"


def supports_output_schema() -> bool:
    """True when the backend honors ADK's output_schema (controlled generation).

    Gemini does it natively; MockLlm simulates it. The LiteLLM providers
    (openai/deepseek/bedrock) are routed through a prompt+parse fallback instead:
    DeepSeek and Bedrock/Anthropic reject schema-constrained `response_format`
    outright, and asking for JSON + validating it works uniformly everywhere.
    """
    return backend() in ("gemini", "mock")


def _first_env(*names: str):
    """First non-empty value among env var `names`, else None."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def _lite_llm(model: str, **kwargs):
    """Build a LiteLlm, with a clear hint if the extra isn't installed.

    Any kwargs with a falsy value (e.g. api_key=None) are dropped so LiteLLM can
    still fall back to its own env-var auto-discovery.
    """
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as e:  # extensions extra not installed
        raise ImportError(
            f"LLM_BACKEND={backend()!r} needs LiteLLM. Run: "
            'pip install "google-adk[extensions]"'
        ) from e
    return LiteLlm(model=model, **{k: v for k, v in kwargs.items() if v})


def _model_id(b: str) -> str:
    """The underlying model id string for backend `b` (no model object built).

    Used as the proxy's dynamic `.model` so ADK's request-build sees a real id —
    crucially a Gemini id under gemini, so `google_search`'s gemini gate passes.
    """
    if b == "mock":
        return "mock"
    if b == "gemini":
        return os.environ.get("GEMINI_MODEL", default_model(Provider.GEMINI))
    if b == "openai":
        return os.environ.get("OPENAI_MODEL", default_model(Provider.OPENAI))
    if b == "deepseek":
        return os.environ.get("DEEPSEEK_MODEL", default_model(Provider.DEEPSEEK))
    if b == "bedrock":
        return os.environ.get("BEDROCK_MODEL", default_model(Provider.BEDROCK))
    raise ValueError(
        f"Unknown LLM_BACKEND={b!r}. Use 'mock', 'gemini', 'openai', "
        "'deepseek', or 'bedrock'."
    )


def _build_real_model(b: str) -> BaseLlm:
    """Build the REAL BaseLlm for backend `b`. Called lazily, on first use under
    each backend (cached per-backend by LazyModel) — never at import."""
    if b == "mock":
        from .mock_llm import MockLlm

        return MockLlm()

    if b == "gemini":
        # Resolve the gemini id through ADK's registry, same as passing the string
        # to LlmAgent(model=...) would — yields a real Gemini BaseLlm we delegate to.
        from google.adk.models.registry import LLMRegistry

        return LLMRegistry.new_llm(_model_id(b))

    if b == "openai":
        # Accept either the LiteLLM-standard name or a plain OPENAI_KEY.
        return _lite_llm(
            _model_id(b),
            api_key=_first_env("OPENAI_API_KEY", "OPENAI_KEY"),
        )

    if b == "deepseek":
        return _lite_llm(
            _model_id(b),
            api_key=_first_env("DEEPSEEK_API_KEY", "DEEPSEEK_KEY"),
        )

    if b == "bedrock":
        return _lite_llm(_model_id(b))

    raise ValueError(
        f"Unknown LLM_BACKEND={b!r}. Use 'mock', 'gemini', 'openai', "
        "'deepseek', or 'bedrock'."
    )


class LazyModel(BaseLlm):
    """A `BaseLlm` proxy that binds the backend PER TURN, not at import.

    `LlmAgent(model=LazyModel())` is resolved by ADK's `canonical_model` to this
    instance directly (it's a BaseLlm), and ADK calls `generate_content_async`
    once per model turn. We read LLM_BACKEND at that moment, build (once, then
    cache) the real underlying model for it, and delegate. So the SAME imported
    agent answers via MockLlm under mock and via Gemini under gemini — chosen at
    run time. The only state is a per-backend cache of real models.
    """

    # Pydantic field declared for completeness; reads go through __getattribute__
    # below so `.model` always reflects the ACTIVE backend's id, not a frozen value.
    model: str = "mock"

    def __getattribute__(self, name):
        # ADK reads `agent.canonical_model.model` at request-build time to gate
        # model-native tools (google_search asserts is_gemini_model on it). Report
        # the live backend's real id so that gate stays correct after a switch.
        if name == "model":
            return _model_id(backend())
        return super().__getattribute__(name)

    def _real(self) -> BaseLlm:
        b = backend()
        cache = object.__getattribute__(self, "_cache")
        if b not in cache:
            cache[b] = _build_real_model(b)
        return cache[b]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Per-backend cache of real models (bypass pydantic; not a declared field).
        object.__setattr__(self, "_cache", {})

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        async for resp in self._real().generate_content_async(llm_request, stream=stream):
            yield resp

    def connect(self, llm_request: LlmRequest):
        return self._real().connect(llm_request)


def get_model() -> BaseLlm:
    """Return the lazy model proxy. Binding the backend happens per turn (see
    `LazyModel`), so importing an agent never freezes LLM_BACKEND."""
    return LazyModel()
