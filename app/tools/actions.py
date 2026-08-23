"""The one state-changing tool: escalation writes, which only ever run after an explicit user confirmation."""

import uuid
from datetime import datetime

from app.db import connect, query, reference_time


def preview(account_id: str, args: dict) -> dict:
    """Build the card the user confirms. Nothing is written here — this is the pre-commit description."""
    ticket_id = args.get("ticket_id") or ""
    ticket = query(
        "SELECT subject FROM tickets WHERE ticket_id = :t AND account_id = :a",
        {"t": ticket_id, "a": account_id},
    )
    subject = ticket[0]["subject"] if ticket else None
    return {
        "id": str(uuid.uuid4()),
        "tool": "create_escalation",
        "args": {
            "ticket_id": ticket_id or None,
            "reason": args.get("reason", ""),
            "summary": args.get("summary", ""),
        },
        "account_id": account_id,
        "ticket_subject": subject,
        "ticket_verified": bool(ticket) if ticket_id else None,
        "preview_text": (
            f"Escalate {ticket_id or 'a new issue'} to a human support agent"
            + (f' ("{subject}")' if subject else "")
            + f".\nReason: {args.get('reason', '')}\nSummary: {args.get('summary', '')}"
        ),
    }


def execute(pending_action: dict) -> dict:
    """Called only from the confirmation node, after the user clicked Confirm."""
    account_id = pending_action["account_id"]
    args = pending_action["args"]
    escalation_id = f"ESC-{uuid.uuid4().hex[:8].upper()}"

    with connect() as conn:
        conn.execute(
            "INSERT INTO escalations VALUES (:id, :account_id, :ticket_id, :reason, :summary, :created_at)",
            {
                "id": escalation_id,
                "account_id": account_id,
                "ticket_id": args.get("ticket_id"),
                "reason": args.get("reason", ""),
                "summary": args.get("summary", ""),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
    return {
        "status": "created",
        "escalation_id": escalation_id,
        "ticket_id": args.get("ticket_id"),
        "reference_time": reference_time(),
        "message": "Escalation created and queued for a human support agent.",
    }


def declined(message: str | None = None) -> dict:
    return {
        "status": "declined",
        "message": message
        or "The user declined the escalation. Nothing was created. Do not retry without a new request.",
    }
