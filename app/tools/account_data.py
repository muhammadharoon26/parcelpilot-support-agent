"""Structured lookups and time calculations over SQLite, always constrained to the session's own account."""

from datetime import datetime

from app.config import AUTHORITY_LABELS
from app.db import query, reference_time

TIME_FORMAT = "%Y-%m-%d %H:%M"

QUERY_TYPES = [
    "account_profile",
    "list_orders",
    "get_order",
    "list_tickets",
    "get_ticket",
    "order_cancellation_facts",
    "pickup_delay_facts",
    "ticket_sla_facts",
]


def _now() -> datetime:
    # Reference time is the dataset snapshot ("2026-08-16 11:00 Asia/Kolkata"), not wall-clock time.
    return datetime.strptime(reference_time().rsplit(" ", 1)[0], TIME_FORMAT)


def _parse(value: str | None) -> datetime | None:
    return datetime.strptime(value, TIME_FORMAT) if value else None


def _minutes_between(start: datetime, end: datetime) -> int:
    return round((end - start).total_seconds() / 60)


def _fetch_order(account_id: str, order_id: str) -> dict | None:
    rows = query(
        "SELECT * FROM orders WHERE order_id = :order_id AND account_id = :account_id",
        {"order_id": order_id, "account_id": account_id},
    )
    return rows[0] if rows else None


def _fetch_ticket(account_id: str, ticket_id: str) -> dict | None:
    rows = query(
        "SELECT * FROM tickets WHERE ticket_id = :ticket_id AND account_id = :account_id",
        {"ticket_id": ticket_id, "account_id": account_id},
    )
    return rows[0] if rows else None


def _tag_historical(ticket: dict) -> dict:
    """Past resolutions may be wrong, so they are never returned as a bare field the model can mistake for policy."""
    resolution = ticket.pop("historical_resolution", None)
    if resolution:
        ticket["historical_resolution"] = {
            "text": resolution,
            "authority_rank": 6,
            "reliability": AUTHORITY_LABELS[6],
            "warning": "Context only. Never sufficient to justify an answer or an action; verify against ranked documents.",
        }
    return ticket


def run(account_id: str, query_type: str, params: dict) -> dict:
    """`account_id` comes from the server session, never from the model's tool arguments."""
    params = params or {}
    handler = _HANDLERS.get(query_type)
    if handler is None:
        return {"error": f"Unknown query_type '{query_type}'. Supported: {QUERY_TYPES}"}
    return handler(account_id, params)


def _account_profile(account_id: str, _: dict) -> dict:
    rows = query("SELECT * FROM accounts WHERE account_id = :account_id", {"account_id": account_id})
    if not rows:
        return {"error": "Account not found"}
    account = rows[0]
    account["has_custom_agreement"] = bool(account.get("contract_file"))
    return {"reference_time": reference_time(), "account": account}


def _list_orders(account_id: str, _: dict) -> dict:
    rows = query(
        "SELECT order_id, carrier, status, booked_at, shipment_fee_inr, notes "
        "FROM orders WHERE account_id = :account_id ORDER BY booked_at DESC",
        {"account_id": account_id},
    )
    return {"reference_time": reference_time(), "orders": rows}


def _get_order(account_id: str, params: dict) -> dict:
    order = _fetch_order(account_id, params.get("order_id", ""))
    if order is None:
        # Deliberately identical for "does not exist" and "belongs to another account" — a
        # distinguishable error would leak the existence of other accounts' orders.
        return {"error": "No such order on this account."}
    return {"reference_time": reference_time(), "order": order}


def _list_tickets(account_id: str, _: dict) -> dict:
    rows = query(
        "SELECT * FROM tickets WHERE account_id = :account_id ORDER BY created_at DESC",
        {"account_id": account_id},
    )
    return {"reference_time": reference_time(), "tickets": [_tag_historical(t) for t in rows]}


def _get_ticket(account_id: str, params: dict) -> dict:
    ticket = _fetch_ticket(account_id, params.get("ticket_id", ""))
    if ticket is None:
        return {"error": "No such ticket on this account."}
    return {"reference_time": reference_time(), "ticket": _tag_historical(ticket)}


def _order_cancellation_facts(account_id: str, params: dict) -> dict:
    order = _fetch_order(account_id, params.get("order_id", ""))
    if order is None:
        return {"error": "No such order on this account."}
    booked, requested = _parse(order["booked_at"]), _parse(order["cancellation_requested_at"])
    return {
        "reference_time": reference_time(),
        "order_id": order["order_id"],
        "status": order["status"],
        "booked_at": order["booked_at"],
        "cancellation_requested_at": order["cancellation_requested_at"],
        "minutes_from_booking_to_cancellation_request": (
            _minutes_between(booked, requested) if booked and requested else None
        ),
        "minutes_from_booking_to_reference_time": _minutes_between(booked, _now()) if booked else None,
        "picked_up_at": order["pickup_actual_at"],
        "shipment_fee_inr": order["shipment_fee_inr"],
        "note": "Facts only. Fee thresholds and waivers come from the ranked documents, not from this tool.",
    }


def _pickup_delay_facts(account_id: str, params: dict) -> dict:
    order = _fetch_order(account_id, params.get("order_id", ""))
    if order is None:
        return {"error": "No such order on this account."}
    window_end, actual = _parse(order["pickup_window_end"]), _parse(order["pickup_actual_at"])
    delay_end = actual or _now()
    # A pickup inside its window produces a negative difference, which a reader can easily mistake
    # for lateness. Report lateness as a flag plus a non-negative figure instead.
    overrun = round(_minutes_between(window_end, delay_end) / 60, 2) if window_end else None
    is_late = overrun is not None and overrun > 0
    return {
        "reference_time": reference_time(),
        "order_id": order["order_id"],
        "status": order["status"],
        "pickup_window_start": order["pickup_window_start"],
        "pickup_window_end": order["pickup_window_end"],
        "pickup_actual_at": order["pickup_actual_at"],
        "pickup_still_outstanding": actual is None,
        "pickup_is_late": is_late,
        "hours_past_pickup_window_end": (max(overrun, 0.0) if overrun is not None else None),
        "hours_remaining_in_pickup_window": (round(-overrun, 2) if overrun is not None and not is_late else None),
        "carrier_fault": bool(order["carrier_fault"]),
        "customer_fault": bool(order["customer_fault"]),
        "shipment_fee_inr": order["shipment_fee_inr"],
        "ten_percent_of_shipment_fee_inr": round(order["shipment_fee_inr"] * 0.1, 2),
        "note": (
            "Facts only. hours_past_pickup_window_end is 0 when the pickup is not late; check "
            "pickup_is_late first. Eligibility thresholds, credit amounts and caps come from the "
            "ranked documents, not from this tool."
        ),
    }


def _ticket_sla_facts(account_id: str, params: dict) -> dict:
    ticket = _fetch_ticket(account_id, params.get("ticket_id", ""))
    if ticket is None:
        return {"error": "No such ticket on this account."}
    account = query("SELECT plan FROM accounts WHERE account_id = :a", {"a": account_id})
    created = _parse(ticket["created_at"])
    return {
        "reference_time": reference_time(),
        "ticket": _tag_historical(ticket),
        "plan": account[0]["plan"] if account else None,
        "minutes_since_ticket_created": _minutes_between(created, _now()) if created else None,
        "note": (
            "Facts only. This tool does not assign a severity or an SLA target — derive severity from the "
            "severity definitions and the response target from the highest-authority applicable document."
        ),
    }


_HANDLERS = {
    "account_profile": _account_profile,
    "list_orders": _list_orders,
    "get_order": _get_order,
    "list_tickets": _list_tickets,
    "get_ticket": _get_ticket,
    "order_cancellation_facts": _order_cancellation_facts,
    "pickup_delay_facts": _pickup_delay_facts,
    "ticket_sla_facts": _ticket_sla_facts,
}
