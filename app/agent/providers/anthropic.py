"""Anthropic adapter: the block format is native, so this only strips fields other providers need."""

from functools import lru_cache

from app.agent.llm import MAX_TOKENS
from app.config import LLM_API_KEY, LLM_MODEL

NATIVE_KEYS = {
    "text": {"type", "text"},
    "tool_use": {"type", "id", "name", "input"},
    "tool_result": {"type", "tool_use_id", "content"},
}


@lru_cache(maxsize=1)
def _client():
    import anthropic

    return anthropic.Anthropic(api_key=LLM_API_KEY)


def complete(system: str, messages: list[dict], tools: list[dict]) -> list[dict]:
    # Drop the fields other providers need (tool_result "name", Gemini's "signature").
    payload = [
        {
            "role": m["role"],
            "content": [{k: v for k, v in b.items() if k in NATIVE_KEYS[b["type"]]} for b in m["content"]],
        }
        for m in messages
    ]
    response = _client().messages.create(
        model=LLM_MODEL, max_tokens=MAX_TOKENS, system=system, tools=tools, messages=payload
    )
    return [
        {"type": "text", "text": b.text}
        if b.type == "text"
        else {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
        for b in response.content
    ]
