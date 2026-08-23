"""Gemini adapter: block format translation, thought-signature replay, and free-tier quota handling."""

import base64
import json
import time
import uuid
from functools import lru_cache

from app.agent.llm import MAX_TOKENS, ModelUnavailable
from app.config import LLM_API_KEYS, LLM_MODELS

RATE_LIMIT_RETRIES = 6
MAX_BACKOFF = 30.0


@lru_cache(maxsize=8)
def _client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _contents(messages: list[dict]) -> list[dict]:
    """Gemini pairs a function response to its call by name, which is why tool_result blocks carry one."""
    contents = []
    for message in messages:
        parts = []
        for block in message["content"]:
            if block["type"] == "text":
                parts.append({"text": block["text"]})
            elif block["type"] == "tool_use":
                part = {"function_call": {"name": block["name"], "args": block["input"]}}
                # Gemini 3 rejects replayed function calls that have lost their thought signature.
                if block.get("signature"):
                    part["thought_signature"] = base64.b64decode(block["signature"])
                parts.append(part)
            elif block["type"] == "tool_result":
                parts.append(
                    {"function_response": {"name": block["name"], "response": json.loads(block["content"])}}
                )
        # Gemini has no "assistant" role and expects tool output back on the user turn.
        contents.append({"role": "model" if message["role"] == "assistant" else "user", "parts": parts})
    return contents


def _tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "function_declarations": [
                {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
                for t in tools
            ]
        }
    ]


def _quota_violation(exc) -> dict:
    for detail in (getattr(exc, "details", None) or {}).get("error", {}).get("details", []):
        if "QuotaFailure" in detail.get("@type", ""):
            return (detail.get("violations") or [{}])[0]
    return {}


def _is_daily_quota(exc) -> bool:
    return "PerDay" in _quota_violation(exc).get("quotaId", "")


def _retry_delay(exc, attempt: int) -> float:
    """Honour the server's own retryDelay when it sends one, else back off exponentially.

    Retries must be able to outlast a full 60s quota window, since one multi-step turn can spend
    the free tier's per-minute request budget on its own.
    """
    for detail in (getattr(exc, "details", None) or {}).get("error", {}).get("details", []):
        if detail.get("@type", "").endswith("RetryInfo"):
            return min(float(str(detail.get("retryDelay", "0s")).rstrip("s")) + 2, MAX_BACKOFF)
    return min(2 ** (attempt + 2), MAX_BACKOFF)


def _failure_message(exc) -> str:
    if exc.code == 429:
        return "Gemini's free-tier rate limit was hit on every configured model. Try again in a moment."
    return f"The model is unavailable right now ({exc.code}). Please try again."


QUOTA_SPENT = (
    "Every configured model and key has spent its free-tier daily quota. Add another key or model "
    "to LLM_MODEL / GEMINI_API_KEY in .env, or wait for the quota to reset."
)

# (key, model) pairs whose daily quota is spent. Free-tier quota is counted per key per model, so
# exhaustion is a property of the pair, not of either one alone. Waiting will not clear it today.
_exhausted: set[tuple[str, str]] = set()


def _generate_with(api_key: str, model: str, contents: list[dict], config: dict):
    """Retry one key/model pair on the per-minute cap, which a single turn can exhaust on its own."""
    from google.genai import errors

    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            return _client(api_key).models.generate_content(model=model, contents=contents, config=config)
        except (errors.ClientError, errors.ServerError) as exc:
            if exc.code == 429 and _is_daily_quota(exc):
                _exhausted.add((api_key, model))
                raise
            if exc.code not in (429, 503) or attempt == RATE_LIMIT_RETRIES - 1:
                raise
            time.sleep(_retry_delay(exc, attempt))
    raise ModelUnavailable("The model did not respond. Please try again.")


def _generate(system: str, messages: list[dict], tools: list[dict]):
    """Walk key/model pairs, skipping any whose daily quota is spent.

    Model-major order: the preferred model is tried on every key before dropping to a weaker one,
    so extra keys buy more of the best model rather than more of the worst.
    """
    from google.genai import errors

    config = {
        "system_instruction": system,
        "tools": _tools(tools),
        "max_output_tokens": MAX_TOKENS,
        # The graph runs the tool loop itself; the SDK must not call functions behind its back.
        "automatic_function_calling": {"disable": True},
        "tool_config": {"function_calling_config": {"mode": "AUTO"}},
    }
    contents = _contents(messages)

    pairs = [(k, m) for m in LLM_MODELS for k in LLM_API_KEYS if (k, m) not in _exhausted]
    if not pairs:
        raise ModelUnavailable(QUOTA_SPENT)

    for api_key, model in pairs:
        try:
            return _generate_with(api_key, model, contents, config)
        except (errors.ClientError, errors.ServerError) as exc:
            if exc.code == 429 and _is_daily_quota(exc):
                continue  # this pair is done for the day; another may still have budget
            raise ModelUnavailable(_failure_message(exc)) from exc
    raise ModelUnavailable(QUOTA_SPENT)


def complete(system: str, messages: list[dict], tools: list[dict]) -> list[dict]:
    response = _generate(system, messages, tools)

    blocks: list[dict] = []
    candidate = response.candidates[0] if response.candidates else None
    for part in (candidate.content.parts or []) if candidate and candidate.content else []:
        if getattr(part, "thought", False):
            continue  # reasoning traces are not conversation content
        if part.function_call:
            signature = getattr(part, "thought_signature", None)
            blocks.append(
                {
                    "type": "tool_use",
                    # Gemini ids are optional; the graph needs a stable one to match results to calls.
                    "id": part.function_call.id or f"call_{uuid.uuid4().hex[:12]}",
                    "name": part.function_call.name,
                    "input": dict(part.function_call.args or {}),
                    # Kept base64 so the block stays JSON-serialisable for the checkpointer.
                    "signature": base64.b64encode(signature).decode() if signature else None,
                }
            )
        elif part.text:
            blocks.append({"type": "text", "text": part.text})
    return blocks or [{"type": "text", "text": ""}]
