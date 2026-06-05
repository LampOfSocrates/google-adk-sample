"""One place to choose the model backend for every agent in this project.

Controlled by the LLM_BACKEND env var (see .env):

    mock     (default) -> MockLlm: free, offline, no tokens. Build all day.
    gemini             -> Gemini via API key (GOOGLE_API_KEY). Costs Gemini quota.
    bedrock            -> AWS Bedrock via LiteLLM. Costs AWS. Needs:
                            pip install "google-adk[extensions]"
                            AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION
                            (and the chosen model enabled in the Bedrock console)

`get_model()` returns either a string (Gemini, resolved by ADK's registry) or a
BaseLlm instance (mock / bedrock). Agent.model accepts both.
"""
import os


def get_model():
    backend = os.environ.get("LLM_BACKEND", "mock").strip().lower()

    if backend == "mock":
        from .mock_llm import MockLlm

        return MockLlm()

    if backend == "gemini":
        # flash-lite has a larger free-tier daily quota than gemini-2.5-flash.
        return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

    if backend == "bedrock":
        from google.adk.models.lite_llm import LiteLlm

        return LiteLlm(
            model=os.environ.get(
                "BEDROCK_MODEL", "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
            )
        )

    raise ValueError(
        f"Unknown LLM_BACKEND={backend!r}. Use 'mock', 'gemini', or 'bedrock'."
    )


def get_search_tool():
    """The web-search tool for the search specialist.

    The built-in google_search only runs on Gemini, so for the mock/bedrock
    backends we substitute an offline function tool. Same AgentTool wiring in
    agent.py either way.
    """
    backend = os.environ.get("LLM_BACKEND", "mock").strip().lower()
    if backend == "gemini":
        from google.adk.tools import google_search

        return google_search

    from .mock_llm import web_search

    return web_search
