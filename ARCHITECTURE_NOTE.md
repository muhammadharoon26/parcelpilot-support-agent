# Architecture Note

One FastAPI service serves the API and the chat UI. Behind it sit a LangGraph agent, a SQLite mirror of the
workbook, and a Chroma index of the six PDFs. No external infrastructure — both stores are local files
rebuilt by two scripts, so the whole thing deploys as a single web service.

```
browser (static/index.html)
  │  POST /chat, POST /actions/{id}/confirm|cancel
FastAPI (app/main.py) ── session.py ── account_id  ← the only source of account identity
  │
LangGraph  agent ⇄ tools → human_confirmation (interrupt)
  │                  │
  │                  ├─ search_documents    → Chroma  (scope-filtered, authority-ranked)
  │                  ├─ query_account_data  → SQLite  (WHERE account_id = session)
  │                  └─ create_escalation   → held until the user clicks Confirm
llm.py → Gemini (or Claude)
```

## Agent design

`app/agent/core.py` is a three-node `StateGraph`:

- **agent** — one model call with the three tool schemas bound.
- **tools** — executes the read-only tools immediately; for `create_escalation` it executes nothing and
  sets `pending_action` instead.
- **human_confirmation** — calls `interrupt()`, which suspends the graph and hands control back to the API
  route. The route returns the pending action to the browser; a click on Confirm or Cancel resumes the
  same run with `Command(resume=...)`, and only then does the write happen.

The approval carries the id of the action it approved, and the node executes only if that id matches the
action actually pending. Without that binding, a confirmation card left on screen while the conversation
moved on would approve whichever action was pending by the time it was clicked — the user consents to one
escalation and a different one is written. The API rejects a superseded id with a 409 and the UI greys the
card out, but the graph-level check is the guarantee: consent is bound to a specific action, not to a
moment in time.

Routing is two plain predicates: agent → tools when the last message contains tool calls, tools →
human_confirmation when a pending action exists. `MemorySaver` keyed by session id persists state across
the pause. Multi-step requests fall out of the loop rather than being scripted — nothing in the graph
knows which tool answers which question.

The state carries a `results_buffer` because of a protocol constraint: every tool call in a turn must be
answered in a single message. When one turn mixes a lookup with an escalation, the lookup's result waits
in the buffer until the confirmation resolves, and both results are emitted together.

## Tool design

Three tools, deliberately shaped so that **facts and rules come from different places**:

| Tool | Returns | Does not |
| --- | --- | --- |
| `search_documents(query)` | ranked chunks + `authority_rank`, `status`, `applies_to` | decide anything |
| `query_account_data(query_type, params)` | records and elapsed-time arithmetic | apply any policy threshold |
| `create_escalation(ticket_id, reason, summary)` | a confirmation card | write on call |

`query_account_data` takes a `query_type` from a fixed enum (8 named queries) rather than model-written
SQL. The computation queries return neutral facts — *minutes from booking to the cancellation request*,
*hours past the pickup window*, *10% of the shipment fee* — and say explicitly that thresholds live in the
documents. So the SOP's 30-minute rule or the agreement's waiver is applied by reasoning over retrieved
text, not baked into a query. Changing a policy means re-ingesting a PDF, not editing SQL.

## Document and structured-data handling

The workbook's sheets are mirrored into SQLite as-is; the README sheet's snapshot time (`2026-08-16 11:00
Asia/Kolkata`) goes into a `meta` table and is the "now" for every time calculation, so answers don't drift
with the wall clock.

PDFs are chunked on section headings (`1. Order cancellation`, `KI-208 - ...`) so a chunk is a
self-contained rule rather than an arbitrary window. Authority metadata is attached at **ingestion** time
from a registry in `config.py`, not inferred at query time — filename to `doc_type`, `status`,
`customer_scope`, `authority_rank`.

## Source reliability and conflict handling

Ranking, highest authority first:

1. the customer's signed agreement — overrides general policy for that account
2. Cancellation & Service Credit SOP v4
3. Support Policy v3 (current)
4. Product Operations Guide / Known Issues — informational, not a policy authority
5. Support Policy v2 (deprecated) — never justifies an answer
6. historical ticket resolutions — untrusted context, never sufficient alone

Rank 1–5 are document metadata. Rank 6 is applied in the data layer: `account_data.py` never returns a
`historical_resolution` as a bare string — it wraps it with its rank and an explicit warning, so a past
wrong answer cannot be mistaken for policy. The dataset has two such traps (TKT-450 asserts a cancellation
fee the Northstar agreement waives; TKT-451 asserts a 3,000-row limit that is really the KI-208 bug), and
both are answered from the governing source instead.

`document_search` sorts by rank before the model sees anything, so the governing source is read first, and
returns the resolution rule alongside the chunks. The system prompt then carries the escalation triggers:
no sufficient source, same-rank conflict, deprecated-or-historical-only support, an uncovered exception,
missing facts the SOP requires, a P1 or a breached target.

## Access control

Enforced at two chokepoints, both below the model:

1. **The schemas have no account parameter.** The model has no vocabulary for naming an account, so there
   is no code path that accepts a model-supplied one. `account_id` is injected by the graph from the
   session.
2. **Every query is scoped server-side.** SQL always carries `WHERE account_id = :session_account_id`;
   Chroma queries always carry `customer_scope IN (general, session_account_id)`. A Northstar session
   asking directly for LumenWorks' credit terms gets general policy and its own agreement, nothing else —
   there's a test for exactly that.

Lookups for a record on another account return the same "no such order on this account" as one that
doesn't exist, so error messages can't be used to probe for other customers' records. Sessions fail
closed, and confirm/cancel resolve the session from the pending action rather than trusting the caller.

## Trade-offs

- **SQLite + local Chroma over Postgres + a vector service.** Zero infra, one deployable unit,
  regenerable from the pack in two commands. It won't survive horizontal scaling — at that point the
  stores move out and the tool interfaces stay the same.
- **`MemorySaver` over a persisted checkpointer.** Conversations and pending actions are in-process, so a
  restart drops them. Right for a demo; a real deployment swaps in the SQLite or Postgres checkpointer,
  which is a one-line change.
- **Gemini over Claude.** The design targeted Claude's native tool use; the free tier that this project
  could actually run on is Gemini's. `llm.py` normalises both to one block format, so the graph, tools and
  prompts are untouched by the switch and `LLM_PROVIDER=anthropic` flips it back. Three Gemini-specific
  details live in `app/agent/providers/gemini.py` and nowhere else: function calls must carry their
  `thought_signature` back into the history or the API rejects the replayed turn; per-minute limits are
  retried with backoff; and because the free tier allows only ~20 requests/day *per key per model* while
  one answer spends 3–6, both the model list and the key list are preference lists. The provider walks
  key/model pairs and skips any pair whose daily quota is spent, model-major so extra capacity buys more
  of the best model rather than more of the worst. Quota exhaustion is a property of the pair, not of
  either alone, which is why it is tracked that way.
- **A fixed query enum over model-written SQL.** Costs some flexibility on unanticipated questions; buys a
  data layer that cannot be talked into reading another tenant's rows.
- **Confirmation as a UI card, not a chat reply.** A card can't be argued with by a "yeah go ahead" that
  the model misreads, and the gate holds even if the model is compromised — it never had the ability to
  execute the write.
- **The source pack is committed to the repo.** The design called for gitignoring it, but a hosted deploy
  has to be able to build its own indexes at release time. The data is synthetic and supplied for this
  assessment.
