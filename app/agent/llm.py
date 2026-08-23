"""Provider-agnostic model call: takes normalised messages + tool schemas, returns normalised content blocks.

The graph speaks one message format (Anthropic-style blocks). Each provider module translates that
format to its own API and back, so switching providers never touches the graph, the tools or the prompt.
"""

from app.config import LLM_API_KEY, LLM_PROVIDER

MAX_TOKENS = 2048


class ModelUnavailable(RuntimeError):
    """Raised for configuration and upstream failures the user should see as a message, not a stack trace."""


def complete(system: str, messages: list[dict], tools: list[dict]) -> list[dict]:
    """Returns content blocks: {"type": "text", ...} and {"type": "tool_use", "id", "name", "input"}."""
    if not LLM_API_KEY:
        key_name = "GEMINI_API_KEY" if LLM_PROVIDER == "gemini" else "ANTHROPIC_API_KEY"
        raise ModelUnavailable(f"{key_name} is not set. Add it to .env and restart the server.")

    if LLM_PROVIDER == "gemini":
        from app.agent.providers import gemini

        return gemini.complete(system, messages, tools)
    if LLM_PROVIDER == "anthropic":
        from app.agent.providers import anthropic

        return anthropic.complete(system, messages, tools)
    raise ValueError(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}")
