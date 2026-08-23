# ParcelPilot Customer Support Agent — Technical Design Document

**Purpose of this doc:** This is a build spec for Claude Code. Follow it closely. Priorities, in order: correctness on access control and confirmation-before-action, source reliability logic, minimal and clean code, working demo. Do not over-engineer. No unnecessary abstraction layers, no microservices, no ORM unless it saves code. One deployable app.

---

## 1. Product Scope

Build a **customer-facing** support chatbot for ParcelPilot. A logged-in customer (mocked login, no real auth needed) asks natural-language questions about their account, orders, contracts, and policies. The agent answers directly when it can, and escalates to a human when it can't.

Bonus problem addressed: **Trust & Reliability** (not the proactive-detection dashboard). Every answer must be traceable to a ranked, reliable source, and the agent must escalate rather than guess when sources conflict or are insufficient.

---

## 2. Tech Stack (keep it this small)

- **Backend:** FastAPI (Python), single service
- **LLM:** Anthropic Claude API, using native tool use (function calling loop)
- **Structured data:** SQLite (loaded once from the provided xlsx via a script). No Postgres needed for a take-home — SQLite is zero-infra and the file itself can ship in the repo (regenerable).
- **Document store:** ChromaDB in persistent local mode (embedded, no server) for vector search over the 6 PDFs
- **Frontend:** One static HTML file + vanilla JS (no React/build step). A chat window that renders assistant text, and a small inline badge per tool call showing tool name + a one-line summary of what it did. FastAPI serves this static file directly — one deployable unit, one URL.
- **Hosting:** Render or Railway (single web service, exposes both API and static frontend)
- **Agent orchestration:** LangGraph (`langgraph` + `langgraph-checkpoint` for `MemorySaver`). Used specifically for its `interrupt()` primitive, which is the standard mechanism for pausing a graph mid-execution and resuming after human confirmation — this is a direct fit for the confirmation-before-action requirement, not a bolted-on abstraction.
- **Dependencies (aim for exactly this list, don't add more):** `fastapi`, `uvicorn`, `anthropic`, `langgraph`, `chromadb`, `pandas`, `openpyxl`, `pdfplumber`, `python-dotenv`, `pydantic`

- **Time-box / fallback plan:** LangGraph is new to the builder. Hard-timebox getting the graph skeleton (agent node + tool node + one conditional edge, running end to end) to ~3-4 hours. If still blocked past that point, fall back to a plain Claude SDK tool-use loop (same tools, same reliability/escalation logic, hand-rolled confirmation gate instead of `interrupt()`) rather than risk an unfinished submission. A working simpler system beats a broken fancier one for this deadline.

---

## 3. Directory Structure

```
parcelpilot-support-agent/
├── README.md
├── .env.example
├── requirements.txt
├── .gitignore
├── data/
│   ├── raw/                     # user drops the 6 PDFs + xlsx here (gitignored)
│   └── processed/               # chroma_db/ and app.sqlite (generated, gitignored)
├── scripts/
│   ├── ingest_documents.py      # PDFs -> chunks -> embeddings -> chroma, with metadata
│   └── load_structured_data.py  # xlsx -> sqlite
├── app/
│   ├── main.py                  # FastAPI app, routes, serves static/
│   ├── config.py                # env vars, reliability rank constants, snapshot time
│   ├── session.py               # mock login -> in-memory session {session_id: account_id}
│   ├── db.py                    # sqlite connection helper (raw SQL, no ORM)
│   ├── models.py                # pydantic request/response schemas
│   ├── agent/
│   │   ├── core.py              # Claude tool-use loop
│   │   ├── tool_schemas.py      # JSON tool schemas passed to Claude
│   │   └── system_prompt.py     # system prompt incl. reliability + escalation rules
│   └── tools/
│       ├── document_search.py   # chroma query, scoped + ranked
│       ├── account_data.py      # sqlite queries, account-scoped server-side
│       └── actions.py           # mocked escalation creation + pending-action store
├── static/
│   └── index.html               # single-file chat UI, shows tool activity inline
└── tests/
    └── test_agent_flow.py       # access control + confirmation-gate sanity tests
```

---

## 4. Data Layer

### 4.1 Structured data (`load_structured_data.py`)
Read each sheet of the provided xlsx into SQLite as-is (accounts, orders, tickets, README/snapshot). Don't redesign the schema — mirror the sheet columns. Read the snapshot timestamp from the README sheet and store it in a small `meta` table (`key`, `value`) so the running app can fetch `reference_time` once at startup instead of hardcoding it.

### 4.2 Document ingestion (`ingest_documents.py`)
Chunk each PDF (by section/heading where possible, else fixed-size ~500 tokens with overlap). For every chunk, attach metadata:

```json
{
  "source_file": "01_Support_Policy_v3_CURRENT.pdf",
  "doc_type": "policy | sop | product_ops | contract",
  "status": "current | deprecated",
  "customer_scope": null or "northstar" or "lumenworks",
  "authority_rank": 1-5   // see section 6
}
```

Store chunks + metadata + embeddings in Chroma. This metadata is what makes the reliability logic work later — get it right at ingestion time rather than trying to infer it at query time.

---

## 5. Access Control (enforce in code, not prompts)

1. `/login` mock endpoint: user picks an account from a dropdown (populated from the `accounts` table). Backend creates a session (in-memory dict is fine for a take-home) mapping `session_id -> account_id`. Session id returned as a cookie or bearer token.
2. **Every tool implementation function takes `account_id` as an argument injected by the backend from the session — never from the LLM's tool-call arguments.** The tool JSON schemas exposed to Claude must NOT include `account_id` as a parameter at all. This is the actual enforcement point: even if the model hallucinates or is prompt-injected into asking for another account's data, there is no code path that accepts an attacker-supplied account_id.
3. Document search results are filtered server-side: return chunks where `customer_scope is null` (general policy/SOP/product docs) OR `customer_scope == session.account_id`. A LumenWorks chunk must never be returned to a Northstar session, regardless of what the model asks for.
4. Structured queries (`account_data.py`) always add `WHERE account_id = :session_account_id` in the SQL, non-negotiably, even if the model's tool call implies a different account.

---

## 6. Source Reliability & Conflict Handling (Trust & Reliability bonus)

### 6.1 Authority ranking (highest wins)
1. **Customer-specific agreement** (Northstar / LumenWorks) — overrides general policy for that account only
2. **Cancellation & Service Credit SOP v4**
3. **Support Policy v3 (current)**
4. **Product Operations Guide / Known Issues** — informational for product-bug questions, not a policy authority
5. **Support Policy v2 (deprecated)** — never used to justify an answer; only surfaced if the model needs to explain "this used to be the rule, it's since changed"
6. **Historical ticket resolutions** — context only, explicitly untrusted, never sufficient on its own to answer or justify an action

### 6.2 Resolver logic (implement as a plain function, not just prompt instructions)
When `document_search` returns multiple chunks relevant to a question, sort by `authority_rank` before handing to the model, and include the rank + status in what's shown to the model so it can reason about which one wins. The system prompt should instruct Claude explicitly: "If a customer-specific agreement addresses this, it overrides the general policy for this account. If sources conflict at the same rank, or the only supporting source is deprecated or a historical ticket, do not answer definitively — escalate."

### 6.3 Escalation triggers
Escalate instead of answering when:
- No source at a sufficient authority level supports a definitive answer
- Two sources at the same rank conflict with no stated resolution rule
- The only supporting evidence is the deprecated policy or a historical ticket resolution
- Customer is asking for an exception/waiver not covered by any document
- The question requires investigating a product bug beyond what the Known Issues doc covers
- The question would require data outside the customer's own account

---

## 7. Agent Design (LangGraph)

`agent/core.py` defines a `StateGraph` rather than a manual loop. Keep it generic — don't hardcode which tools get called for which question, that defeats the "don't hardcode example answers" requirement.

### 7.1 State schema
```python
class AgentState(TypedDict):
    messages: list          # conversation history, Claude message format
    account_id: str         # injected from session, never from the model
    pending_action: dict | None   # set when create_escalation is requested
```

### 7.2 Nodes
- **`agent`** — calls Claude with the 3 tool schemas bound and the current `messages`. Appends the response to state.
- **`tools`** — executes any `tool_use` blocks from the last agent message. For `search_documents` and `query_account_data`, runs the real function immediately and appends a `tool_result`. For `create_escalation`, does **not** execute — instead sets `state["pending_action"]` and routes to `human_confirmation`.
- **`human_confirmation`** — calls `interrupt({"pending_action": state["pending_action"]})`, which pauses graph execution and returns control to the API layer. The frontend renders the Confirm/Cancel card from this payload. When the user responds, the backend resumes the graph with the confirmation result, which then performs (or skips) the real write and appends the corresponding `tool_result`, then routes back to `agent`.

### 7.3 Edges
- `agent → tools` if the last message contains tool calls, else `agent → END` (final answer ready)
- `tools → human_confirmation` if the pending tool call was `create_escalation`, else `tools → agent` (continue reasoning with the tool result)
- `human_confirmation → agent` after resume

### 7.4 Checkpointing
Use `MemorySaver` as the checkpointer (in-process, fine for a take-home — no external DB needed). This is required for `interrupt()`/resume to work, since the graph needs to persist state across the pause. Each chat session's `thread_id` (session_id) keys its own checkpoint history.

### 7.1 Tools (exactly 3)

**`search_documents(query: str)`**
Vector search over Chroma, server-side filtered to (general docs + this session's customer_scope), returned sorted by authority_rank. Returns chunk text + source_file + doc_type + status + authority_rank for each result.

**`query_account_data(query_type: str, params: object)`**
Structured lookup/calculation against SQLite, always scoped to the session's account_id server-side. Support the query types needed to answer things like "find this order," "is this ticket past SLA," "what's the contract tier for this account." Keep it as a small number of well-named query types rather than letting the model write raw SQL (raw SQL from an LLM into your DB is a bad idea for a take-home that's graded on access control).

**`create_escalation(ticket_id: str, reason: str, summary: str)`**
Mocked action. Does **not** execute immediately (see section 8). Writes to a local `escalations` table only after explicit user confirmation.

---

### 7.5 LangGraph 101 (just what you need for this project)

You don't need to read the full LangGraph docs. These five concepts cover everything this design uses.

**1. `StateGraph` — the graph itself, typed by your state schema**
```python
from langgraph.graph import StateGraph, END

graph = StateGraph(AgentState)
```
Think of it as a container you register nodes and edges into, then compile into something runnable.

**2. `add_node` — register a function as a node**
```python
graph.add_node("agent", agent_node)
graph.add_node("tools", tools_node)
graph.add_node("human_confirmation", human_confirmation_node)
graph.set_entry_point("agent")
```
Every node function has the same shape: takes the current `AgentState`, returns a dict of the fields it wants to update. LangGraph merges that dict into state automatically. Example:
```python
def agent_node(state: AgentState) -> dict:
    response = call_claude(state["messages"], tools=TOOL_SCHEMAS)
    return {"messages": state["messages"] + [response]}
```

**3. `add_conditional_edges` — routing logic between nodes**
```python
graph.add_conditional_edges(
    "agent",
    route_after_agent,   # a plain function returning a string key
    {"tools": "tools", "end": END},
)
graph.add_conditional_edges(
    "tools",
    route_after_tools,
    {"confirm": "human_confirmation", "continue": "agent"},
)
graph.add_edge("human_confirmation", "agent")
```
`route_after_agent` and `route_after_tools` are just plain Python functions you write, e.g.:
```python
def route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if has_tool_calls(last) else "end"

def route_after_tools(state: AgentState) -> str:
    return "confirm" if state.get("pending_action") else "continue"
```
This is the entire "state machine" — a few node functions and a few routing functions. No magic beyond this.

**4. `interrupt()` — pause the graph and wait for a human**
```python
from langgraph.types import interrupt

def human_confirmation_node(state: AgentState) -> dict:
    decision = interrupt({"pending_action": state["pending_action"]})
    # execution pauses HERE until the graph is resumed with a value
    if decision == "confirm":
        result = execute_escalation(state["pending_action"])
    else:
        result = {"status": "declined"}
    return {"messages": state["messages"] + [tool_result_message(result)], "pending_action": None}
```
When `interrupt()` is hit, the graph stops and control returns to your FastAPI route, which returns the `pending_action` payload to the frontend. When the user clicks Confirm/Cancel, you resume the exact same graph run:
```python
from langgraph.types import Command

graph.invoke(Command(resume="confirm"), config={"configurable": {"thread_id": session_id}})
```
That resumes execution right at `interrupt()`, with `decision` now equal to `"confirm"`.

**5. `MemorySaver` — required for `interrupt()`/resume to work**
```python
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()
app = graph.compile(checkpointer=checkpointer)
```
`thread_id` (use your `session_id`) is how the checkpointer knows which conversation's state to resume. Without a checkpointer, `interrupt()` has nothing to persist to and resume won't work.

**Putting it together — the whole compiled graph:**
```python
app = graph.compile(checkpointer=checkpointer)

# first call for a new message:
result = app.invoke({"messages": [...], "account_id": account_id}, config={"configurable": {"thread_id": session_id}})
# if result contains an interrupt, surface pending_action to the frontend and stop here

# after user clicks Confirm:
result = app.invoke(Command(resume="confirm"), config={"configurable": {"thread_id": session_id}})
```

That's genuinely the whole framework surface area you need. Everything else (tool execution logic, reliability ranking, access control) lives in plain Python functions the nodes call, exactly as designed in the rest of this doc.

---

## 8. Confirmation-Before-Action (hard enforcement, not prompt-based)

Do not rely on the model to "ask nicely before acting." Enforce it structurally:

1. When Claude emits a `tool_use` block for `create_escalation`, the backend does **not** run the real write. It returns a `pending_action` object to the frontend: `{ id, tool: "create_escalation", args, preview_text }`.
2. The frontend renders this as a card with **Confirm** / **Cancel** buttons — not as agent text the user has to reply "yes" to. This removes any dependency on the model correctly interpreting a casual "yeah go ahead."
3. Only a click on **Confirm** calls a separate backend endpoint (`POST /actions/{id}/confirm`) that performs the actual mocked write (append to `escalations` table) and returns a `tool_result` completing the original Claude conversation turn.
4. **Cancel** calls `POST /actions/{id}/cancel`, and a `tool_result` is returned to Claude indicating the user declined, so the model can respond appropriately.

This is the single most important design decision to call out in the demo video — it's a real guarantee, not a prompt-engineered one.

---

## 9. Multi-Step Example (for your own testing, not to hardcode)

"Can Northstar cancel ORD-1001 without a fee?" should trigger, in some order: `query_account_data` to look up the order and confirm it belongs to the logged-in session's account, `search_documents` to pull the Northstar agreement's cancellation terms and the general Cancellation SOP, a comparison resolving which one governs (agreement wins if it addresses this), and a final answer citing which source it relied on — or an escalation if the agreement is silent and the SOP alone isn't a confident enough basis.

---

## 10. API Surface (keep minimal)

```
POST /login              { account_id } -> { session_token }
POST /chat                { message }    -> { reply, tool_calls[], pending_action? }
POST /actions/{id}/confirm -> { result }
POST /actions/{id}/cancel  -> { result }
GET  /accounts            -> list of accounts for the login dropdown (mock auth UI)
```

---

## 11. What to Leave Out (say this explicitly in your Product Note)

- Real authentication (mocked login is fine and stated as such)
- Problem 1 (proactive issue detection dashboard) — note it as a prioritized next step instead
- Raw SQL generation by the model
- Any multi-tenant admin/ops UI

---

## 12. Code Quality Bar

- Every file has a one-line docstring at the top stating its single responsibility
- No file over ~150 lines; if a tool implementation is getting long, that's a signal to simplify the query types, not to add abstraction layers
- Type hints throughout, pydantic models for all API request/response bodies
- Comments should explain *why*, not restate the code (e.g. explain why account_id is never taken from the model, not what a for-loop does)
- No dead code, no commented-out alternate implementations left in

---

## 13. README.md Requirements

Must let a stranger clone and run this in under 3 minutes:
1. Clone repo
2. `cp .env.example .env` and add `ANTHROPIC_API_KEY`
3. `pip install -r requirements.txt`
4. Drop the 6 PDFs + xlsx into `data/raw/`
5. `python scripts/load_structured_data.py` then `python scripts/ingest_documents.py`
6. `uvicorn app.main:app --reload`
7. Open `http://localhost:8000`, pick an account from the dropdown, start chatting

Include one line at the top stating the hosted demo URL, and one line noting the architecture note / product note are in the repo root.

---

## 14. Deliverables Checklist (map back to submission requirements)

- [ ] Public repo with this structure and a 3-minute README
- [ ] Hosted URL (Render/Railway)
- [ ] 5-min demo video: architecture, live demo, key decisions (esp. section 5 and section 8)
- [ ] `ARCHITECTURE_NOTE.md`: agent design, tool design, doc/structured-data handling, reliability & conflict handling, trade-offs
- [ ] `PRODUCT_NOTE.md`: Trust & Reliability approach, what's next (incl. Problem 1), what was left out, one usefulness metric (suggest: % of customer queries resolved without escalation while remaining correct — track escalation rate as a quality signal, not just a cost signal)
- [ ] AI tool usage note (Claude Code usage described plainly)
