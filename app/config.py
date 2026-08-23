"""Environment settings, filesystem paths, and the source-authority registry shared by ingestion and retrieval."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
SQLITE_PATH = PROCESSED_DIR / "app.sqlite"
CHROMA_PATH = PROCESSED_DIR / "chroma_db"
CHROMA_COLLECTION = "parcelpilot_docs"

# Gemini is the default because its free tier supports the function calling this agent needs.
# Set LLM_PROVIDER=anthropic to switch; nothing outside app/agent/llm.py changes.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

# Gemini's free tier caps each model at ~20 requests/day and one multi-step answer spends several,
# so the default is a preference list: exhaust one model's daily quota and the next takes over.
_DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3-flash-preview",
    "anthropic": "claude-sonnet-5",
}
def _csv(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


LLM_MODELS = _csv(os.getenv("LLM_MODEL") or _DEFAULT_MODELS.get(LLM_PROVIDER, ""))
LLM_MODEL = LLM_MODELS[0] if LLM_MODELS else ""

# Keys are a list for the same reason models are: free-tier quota is per key per model, so a
# second key doubles the budget. One key is the normal case and behaves exactly as before.
LLM_API_KEYS = _csv(os.getenv("GEMINI_API_KEY" if LLM_PROVIDER == "gemini" else "ANTHROPIC_API_KEY", ""))
LLM_API_KEY = LLM_API_KEYS[0] if LLM_API_KEYS else ""

# Chroma metadata values must be scalars, so "general" stands in for "no customer scope".
GENERAL_SCOPE = "general"

# Authority ranking, lowest number wins. Rank 6 (historical ticket resolutions) is not a
# document — it lives in SQLite and is tagged untrusted by the account-data tool instead.
DOCUMENT_REGISTRY: dict[str, dict[str, str | int]] = {
    "05_Northstar_Logistics_Enterprise_Agreement.pdf": {
        "doc_type": "contract",
        "status": "current",
        "customer_scope": "ACCT-001",
        "authority_rank": 1,
    },
    "06_LumenWorks_Service_Agreement.pdf": {
        "doc_type": "contract",
        "status": "current",
        "customer_scope": "ACCT-002",
        "authority_rank": 1,
    },
    "03_Cancellation_and_Service_Credit_SOP_v4.pdf": {
        "doc_type": "sop",
        "status": "current",
        "customer_scope": GENERAL_SCOPE,
        "authority_rank": 2,
    },
    "01_Support_Policy_v3_CURRENT.pdf": {
        "doc_type": "policy",
        "status": "current",
        "customer_scope": GENERAL_SCOPE,
        "authority_rank": 3,
    },
    "04_Product_Operations_Guide_and_Known_Issues.pdf": {
        "doc_type": "product_ops",
        "status": "current",
        "customer_scope": GENERAL_SCOPE,
        "authority_rank": 4,
    },
    "02_Support_Policy_v2_DEPRECATED.pdf": {
        "doc_type": "policy",
        "status": "deprecated",
        "customer_scope": GENERAL_SCOPE,
        "authority_rank": 5,
    },
}

AUTHORITY_LABELS: dict[int, str] = {
    1: "Customer-specific signed agreement (overrides general policy for this account)",
    2: "Current SOP",
    3: "Current support policy",
    4: "Product documentation (informational, not a policy authority)",
    5: "DEPRECATED policy (must not justify an answer)",
    6: "Historical ticket resolution (untrusted context only)",
}
