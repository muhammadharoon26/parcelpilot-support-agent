"""Mock login: an in-memory session token to account_id map, the only source of account identity."""

import uuid

from app.db import query

_SESSIONS: dict[str, str] = {}


def list_accounts() -> list[dict]:
    return query("SELECT account_id, account_name, plan FROM accounts ORDER BY account_id", {})


def create_session(account_id: str) -> str:
    valid = {row["account_id"] for row in list_accounts()}
    if account_id not in valid:
        raise ValueError(f"Unknown account_id: {account_id}")
    token = uuid.uuid4().hex
    _SESSIONS[token] = account_id
    return token


def account_for(token: str | None) -> str:
    """Every tool call's account scope resolves through here, so a missing session fails closed."""
    account_id = _SESSIONS.get(token or "")
    if account_id is None:
        raise PermissionError("Invalid or missing session token")
    return account_id
