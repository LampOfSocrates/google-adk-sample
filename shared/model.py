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

`get_model()` returns either a string (Gemini, resolved by ADK's registry) or a
BaseLlm instance (mock / openai / deepseek / bedrock). Agent.model accepts both.

`backend()` / `is_mock()` / `is_gemini()` let app code branch on the active
backend WITHOUT re-reading the env var everywhere. Gate Gemini-only built-in
tools (google_search, native PDF upload) on `is_gemini()` — NOT on `not is_mock()`,
since openai/deepseek/bedrock are also non-Gemini and can't run those tools.
"""
import os


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


def get_model():
    b = backend()

    if b == "mock":
        from .mock_llm import MockLlm

        return MockLlm()

    if b == "gemini":
        # flash-lite has a larger free-tier daily quota than gemini-2.5-flash.
        return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

    if b == "openai":
        # Accept either the LiteLLM-standard name or a plain OPENAI_KEY.
        return _lite_llm(
            os.environ.get("OPENAI_MODEL", "openai/gpt-4o-mini"),
            api_key=_first_env("OPENAI_API_KEY", "OPENAI_KEY"),
        )

    if b == "deepseek":
        return _lite_llm(
            os.environ.get("DEEPSEEK_MODEL", "deepseek/deepseek-chat"),
            api_key=_first_env("DEEPSEEK_API_KEY", "DEEPSEEK_KEY"),
        )

    if b == "bedrock":
        return _lite_llm(
            os.environ.get(
                "BEDROCK_MODEL", "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
            )
        )

    raise ValueError(
        f"Unknown LLM_BACKEND={b!r}. Use 'mock', 'gemini', 'openai', "
        "'deepseek', or 'bedrock'."
    )
