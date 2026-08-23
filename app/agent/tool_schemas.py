"""The three tool schemas exposed to Claude. Note that none of them accepts an account_id."""

from app.tools.account_data import QUERY_TYPES

# account_id is deliberately absent from every schema below. The model has no vocabulary for
# naming an account, so a hallucinated or injected account can never reach the data layer.
TOOL_SCHEMAS = [
    {
        "name": "search_documents",
        "description": (
            "Search ParcelPilot's policies, SOPs, product documentation and the signed customer agreement "
            "that applies to this session's account. Results are ranked by source authority (1 is highest) "
            "and include each source's status. Use this for any rule, entitlement, fee, credit, SLA target "
            "or known-issue question. Results are already scoped to the logged-in customer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language description of the rule or term you need, e.g. "
                    "'cancellation fee for a booked shipment before pickup'.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_account_data",
        "description": (
            "Look up or compute facts about the logged-in customer's own account, orders and tickets. "
            "Returns raw facts and elapsed-time calculations only; it never applies policy thresholds, so "
            "pair it with search_documents to decide what the facts mean. All results are already scoped "
            "to the logged-in customer's account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": QUERY_TYPES,
                    "description": (
                        "account_profile: plan, status, CSM, whether a custom agreement exists. "
                        "list_orders / get_order: order records. "
                        "list_tickets / get_ticket: ticket records, including untrusted historical resolutions. "
                        "order_cancellation_facts: order status and minutes from booking to the cancellation request. "
                        "pickup_delay_facts: hours past the pickup window, fault flags and shipment fee. "
                        "ticket_sla_facts: plan and minutes elapsed since the ticket was created."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": "Query parameters: {'order_id': 'ORD-1001'} or {'ticket_id': 'TKT-501'}. "
                    "Omit or pass {} for account_profile, list_orders and list_tickets.",
                    "properties": {
                        "order_id": {"type": "string"},
                        "ticket_id": {"type": "string"},
                    },
                },
            },
            "required": ["query_type"],
        },
    },
    {
        "name": "create_escalation",
        "description": (
            "Hand the request to a human support agent. Call this when the sources do not support a "
            "definitive answer, when sources conflict without a resolution rule, when the customer asks "
            "for an exception or waiver no document covers, when a P1 or an SLA breach needs a human, or "
            "when the customer asks to be escalated. This is a state-changing action: it is NOT executed "
            "when you call it. The user is shown a confirmation card and must approve it first, so call it "
            "once and then wait rather than asking the customer for permission in your own text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Existing ticket to escalate, if the request relates to one. Omit otherwise.",
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason this needs a human, e.g. 'requested fee waiver not covered by "
                    "the agreement' or 'P1 SLA target already breached'.",
                },
                "summary": {
                    "type": "string",
                    "description": "Two or three sentences for the human agent: what the customer asked, which "
                    "sources you checked, and what remains unresolved.",
                },
            },
            "required": ["reason", "summary"],
        },
    },
]
