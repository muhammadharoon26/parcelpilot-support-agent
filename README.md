# ParcelPilot Customer Support Agent

A customer-facing support chatbot for ParcelPilot. A logged-in customer asks natural-language questions
about their orders, contract terms, service credits and support SLAs. The agent answers from ranked
sources, names the source it relied on, and escalates to a human rather than guessing.

**Hosted demo:** <https://parcelpilot-support-agent-cs35.onrender.com/> (free tier, so the first request
after an idle period takes about a minute to wake the service)
**Notes:** [ARCHITECTURE_NOTE.md](ARCHITECTURE_NOTE.md) · [PRODUCT_NOTE.md](PRODUCT_NOTE.md) · [AI_TOOL_USAGE.md](AI_TOOL_USAGE.md)

The two guarantees worth looking at first are enforced in code, not in the prompt:

- **Access control** — tool schemas have no `account_id` parameter at all, so a hallucinated or
  prompt-injected account can never reach the data layer ([tool_schemas.py](app/agent/tool_schemas.py)).
- **Confirmation before action** — the escalation write is held behind LangGraph's `interrupt()` and only
  runs after a click on Confirm ([core.py](app/agent/core.py)).

## Run it in 3 minutes

```bash
git clone <repo-url> && cd parcelpilot-support-agent
```

```bash
pip install -r requirements.txt
```

Get a free Gemini API key at <https://aistudio.google.com/apikey>, then:

```bash
cp .env.example .env
```

Put the key in `.env` as `GEMINI_API_KEY=...`. Build the two local stores (the source pack already ships
in `data/raw/`):

```bash
python scripts/load_structured_data.py && python scripts/ingest_documents.py
```

```bash
uvicorn app.main:app --reload
```

Open <http://localhost:8000>, pick an account from the dropdown, and start chatting.

> First run of `ingest_documents.py` downloads a ~80 MB embedding model into a local cache.

> **Free-tier quotas.** Gemini's free tier allows roughly 20 requests/day *per key per model*, and one
> multi-step answer spends 3–6. Both `LLM_MODEL` and `GEMINI_API_KEY` are therefore comma-separated
> preference lists: the agent walks key/model pairs and skips any whose daily quota is spent, trying the
> preferred model on every key before dropping to a weaker one. Four models on one key is roughly 80
> calls a day. Per-minute limits are retried with backoff instead, since those do clear.

## Try these

Sign in as **Northstar Logistics** and ask:

- `Can I cancel ORD-1001 without a cancellation fee? Explain why.` — the Enterprise Agreement waives the
  fee that the SOP would otherwise charge, and the answer says so.
- `A past ticket told us a 250 rupee fee applied after 30 minutes. Was that right?` — TKT-450's historical
  resolution contradicts the agreement; the agent should not repeat it.
- `Is TKT-501 past its first-response target?` — the agreement's 15-minute P1 target governs, not the
  policy's 30 minutes, so the target is breached.
- `Please escalate TKT-501.` — a confirmation card appears; nothing is written until you click Confirm.

Then switch to **LumenWorks** and ask `What credit am I owed for ORD-2002?` — its agreement replaces the
SOP's default credit with a fixed INR 300. Asking either account about the other's terms returns nothing.

## Tests

```bash
python -m pytest tests/ -q
```

Covers account scoping, cross-account document isolation and the confirmation gate. The model is stubbed
at the `llm.complete` boundary, so the suite runs without an API key.

## Layout

```
app/
  agent/    core.py (LangGraph), llm.py + providers/, tool_schemas.py, system_prompt.py
  tools/    document_search.py, account_data.py, actions.py
  main.py   FastAPI routes + static UI      config.py  paths, authority ranks, provider
  db.py     SQLite helper                   session.py mock login
scripts/    load_structured_data.py (xlsx -> SQLite), ingest_documents.py (PDFs -> Chroma)
static/     index.html — single-file chat UI with inline tool badges
data/raw/   the supplied source pack (committed so a fresh deploy can build its indexes)
```

## Switching model provider

Gemini is the default because its free tier supports the function calling the tool loop needs. Only
[app/agent/providers/](app/agent/providers) knows the difference — to use Claude instead, set
`LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` in `.env`.
