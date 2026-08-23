"""Sanity tests for the two guarantees that must hold in code: account scoping and the confirmation gate."""

import json
import uuid

import pytest

from app.agent import core
from app.agent.tool_schemas import TOOL_SCHEMAS
from app.db import query
from app.tools import account_data, document_search

NORTHSTAR, LUMENWORKS = "ACCT-001", "ACCT-002"


# --- Access control -------------------------------------------------------


def test_order_from_another_account_is_not_returned():
    assert "error" in account_data.run(NORTHSTAR, "get_order", {"order_id": "ORD-2001"})
    assert "order" in account_data.run(LUMENWORKS, "get_order", {"order_id": "ORD-2001"})


def test_listings_only_contain_the_sessions_own_records():
    own_orders = {r["order_id"] for r in query("SELECT order_id FROM orders WHERE account_id = :a", {"a": NORTHSTAR})}
    listed = {r["order_id"] for r in account_data.run(NORTHSTAR, "list_orders", {})["orders"]}
    assert listed == own_orders

    own_tickets = {r["ticket_id"] for r in query("SELECT ticket_id FROM tickets WHERE account_id = :a", {"a": NORTHSTAR})}
    assert {r["ticket_id"] for r in account_data.run(NORTHSTAR, "list_tickets", {})["tickets"]} == own_tickets


def test_computed_facts_are_also_scoped():
    for query_type in ["order_cancellation_facts", "pickup_delay_facts"]:
        assert "error" in account_data.run(NORTHSTAR, query_type, {"order_id": "ORD-2002"})
    assert "error" in account_data.run(NORTHSTAR, "ticket_sla_facts", {"ticket_id": "TKT-502"})


def test_document_search_never_returns_another_customers_agreement():
    # Query deliberately names the other customer's terms; the filter is server-side so it cannot match.
    results = document_search.search(NORTHSTAR, "LumenWorks fixed INR 300 failed pickup credit")["results"]
    assert results
    assert all(r["source"] != "your signed Service Agreement" for r in results)
    # Nothing the model receives may carry a raw filename.
    assert "source_file" not in json.dumps(results)
    assert ".pdf" not in json.dumps(results)


def test_account_without_an_agreement_sees_only_general_documents():
    results = document_search.search("ACCT-003", "cancellation fee waiver")["results"]
    assert all(r["applies_to"] == "all customers" for r in results)


def test_results_are_sorted_by_authority_and_deprecated_is_labelled():
    results = document_search.search(NORTHSTAR, "first response target for a P1 incident")["results"]
    assert [r["authority_rank"] for r in results] == sorted(r["authority_rank"] for r in results)
    for r in results:
        if r["source"] == "the superseded Support Policy v2":
            assert r["status"] == "deprecated"


def test_no_tool_schema_exposes_account_id():
    """The enforcement point: the model has no parameter with which to name an account."""
    assert "account_id" not in json.dumps(TOOL_SCHEMAS)


def test_on_time_pickups_never_report_negative_lateness():
    """A negative 'hours past window' reads as lateness; only genuinely late pickups get a figure."""
    on_time = account_data.run(NORTHSTAR, "pickup_delay_facts", {"order_id": "ORD-1001"})
    assert on_time["pickup_is_late"] is False
    assert on_time["hours_past_pickup_window_end"] == 0.0

    late = account_data.run(LUMENWORKS, "pickup_delay_facts", {"order_id": "ORD-2002"})
    assert late["pickup_is_late"] is True
    assert late["hours_past_pickup_window_end"] == 4.5


def test_historical_resolutions_are_tagged_untrusted():
    ticket = account_data.run(NORTHSTAR, "get_ticket", {"ticket_id": "TKT-450"})["ticket"]
    assert ticket["historical_resolution"]["authority_rank"] == 6
    assert "Context only" in ticket["historical_resolution"]["warning"]


# --- Confirmation gate ----------------------------------------------------


@pytest.fixture
def escalating_model(monkeypatch):
    """Stub the model: first turn asks to escalate, second turn (after the decision) replies with text.

    Stubbing at the llm boundary keeps these tests provider-independent and free to run without a key.
    """
    calls = {"n": 0}

    def complete(system, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return [
                {
                    "type": "tool_use",
                    "id": "call_test",
                    "name": "create_escalation",
                    "input": {"ticket_id": "TKT-501", "reason": "P1 outage", "summary": "Escalate."},
                }
            ]
        return [{"type": "text", "text": "Done."}]

    monkeypatch.setattr(core.llm, "complete", complete)
    return calls


def _escalation_count() -> int:
    return query("SELECT COUNT(*) AS n FROM escalations", {})[0]["n"]


def test_action_pauses_for_confirmation_and_writes_nothing(escalating_model):
    before = _escalation_count()
    result = core.start_turn(uuid.uuid4().hex, NORTHSTAR, "Please escalate TKT-501")

    assert result["pending_action"] is not None
    assert result["pending_action"]["tool"] == "create_escalation"
    assert result["reply"] == ""
    assert _escalation_count() == before, "escalation was written before the user confirmed"


def test_cancel_leaves_the_database_untouched(escalating_model):
    session = uuid.uuid4().hex
    before = _escalation_count()
    pending = core.start_turn(session, NORTHSTAR, "Please escalate TKT-501")["pending_action"]

    result = core.resume_turn(session, "cancel", pending["id"])
    assert _escalation_count() == before
    assert result["pending_action"] is None
    assert any(c["summary"] == "declined" for c in result["tool_calls"])


def test_confirm_performs_the_write_exactly_once(escalating_model):
    session = uuid.uuid4().hex
    before = _escalation_count()
    pending = core.start_turn(session, NORTHSTAR, "Please escalate TKT-501")["pending_action"]

    result = core.resume_turn(session, "confirm", pending["id"])
    assert _escalation_count() == before + 1
    assert result["reply"] == "Done."

    row = query("SELECT * FROM escalations ORDER BY rowid DESC LIMIT 1", {})[0]
    assert row["account_id"] == NORTHSTAR, "escalation must be filed against the session's account"
    assert row["ticket_id"] == "TKT-501"


def test_confirming_a_superseded_card_does_not_execute_the_newer_action(monkeypatch):
    """A stale card must approve only what it showed, never whatever is pending by the time it is clicked."""
    plan = [
        [{"type": "tool_use", "id": "a", "name": "create_escalation",
          "input": {"ticket_id": "TKT-501", "reason": "first", "summary": "first"}}],
        [{"type": "tool_use", "id": "b", "name": "create_escalation",
          "input": {"ticket_id": "TKT-504", "reason": "second", "summary": "second"}}],
        [{"type": "text", "text": "done"}],
    ]
    calls = {"n": 0}

    def complete(system, messages, tools):
        calls["n"] += 1
        return plan[min(calls["n"] - 1, len(plan) - 1)]

    monkeypatch.setattr(core.llm, "complete", complete)

    session = uuid.uuid4().hex
    stale = core.start_turn(session, NORTHSTAR, "escalate TKT-501")["pending_action"]
    core.start_turn(session, NORTHSTAR, "actually escalate TKT-504")  # supersedes the first card

    before = _escalation_count()
    core.resume_turn(session, "confirm", stale["id"])
    assert _escalation_count() == before, "a superseded card must not execute the action that replaced it"
