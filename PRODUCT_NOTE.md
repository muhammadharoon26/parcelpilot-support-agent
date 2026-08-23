# Product Note

## The problem I chose: Trust and Reliability

A support agent that is confidently wrong is worse than no agent. The customer acts on the wrong answer,
and the one time it happens loudly, the team stops trusting every answer it gave before. So the product
question isn't "how often can it answer", it's "can you tell, from the answer itself, whether to trust
it."

Three decisions follow from that.

**Sources are ranked, and the ranking is data, not hunches.** Every chunk carries an authority rank assigned
at ingestion. A customer's signed agreement outranks the SOP, which outranks current policy, which
outranks product docs. The deprecated policy and historical ticket resolutions sit at the bottom and can
never justify an answer. The supplied pack is deliberately booby-trapped TKT-450 tells Northstar a
cancellation fee applies when their agreement waives it, TKT-451 blames a 3,000 row plan limit for what is
really a known bug against a 5,000 row limit, and the ranking is what stops the agent repeating either.

**Every answer names its source.** Not a footnote: the answer says "your Enterprise Agreement waives this,
which is why it differs from our standard policy." That is the difference between an answer a customer can
check and one they have to take on faith. It also makes the agent auditable by the support team, who can
see which document produced a claim without re-deriving it.

**Escalation is a designed outcome, not a failure path.** The agent escalates on same-rank conflicts,
uncovered exceptions, missing facts the SOP requires before a state-changing decision, P1s, and breached
targets. Escalating costs one human touch. A wrong confident answer costs a refund, a trust problem, and
sometimes a customer.

Separately, the **confirmation gate** is a trust decision as much as a safety one. The escalation write is
structurally unreachable until a human clicks Confirm — it is not the model promising to ask first. The
model can be wrong, jailbroken or prompt-injected and still cannot create anything. That property is worth
more than any accuracy number, because it holds when the model is at its worst rather than its best.

## The metric I'd judge it on

**Resolution rate at zero known-incorrect answers** — the share of customer queries closed without a human,
measured only while the incorrect-answer count stays at zero.

Not deflection rate. Deflection rewards answering, and the cheapest way to raise it is to guess. Pairing
the two makes the tradeoff explicit: escalation rate becomes a quality dial, not just a cost line. If
escalations rise, the honest question is which source gap caused them — usually a real hole in the
documentation, which is worth knowing.

The supporting signal is **escalation reason mix**. Escalations from "no source covers this" point at
documentation to write. Escalations from "sources conflict" point at contracts and policies to reconcile.
Both make the *company* better, not just the bot.

## What I'd build next, in priority order

1. **Problem 1: proactive issue detection.** The reactive agent only helps once someone asks, and the data
   already shows the pattern — TKT-502 and TKT-451 are both KI-208, TKT-501 is a P1 that has already blown
   its 15-minute contractual target, TKT-505 is a credential exposure sitting untouched for 2.5 hours. An
   internal view that clusters tickets by underlying issue, ranks by SLA headroom against each account's
   *contractual* target, and flags issues spanning multiple customers would catch all three today. It
   reuses this system's ranking and scoping wholesale, only the UI and a clustering pass are new.
2. **An answer-quality feedback loop.** Log every answer with the chunks that produced it, let support
   agents mark wrong ones, and treat those as a regression suite. Right now correctness is only checked
   by hand.
3. **Real auth and per-role scoping.** Login is mocked. The enforcement points are already in the right
   place — one function resolves account identity — so this is swapping the session layer, not a
   redesign. Internal staff roles slot in as a wider scope on the same chokepoints.
4. **Contract ingestion that survives real contracts.** Two clean one-page agreements make heading-based
   chunking look better than it is. Real agreements have amendments, effective dates and clauses that
   supersede clauses. Rank alone won't resolve that, the ranking needs a time dimension.
5. **More action tools.** Ticket updates, follow-up tasks, credit requests — all behind the same
   confirmation gate, which was built generic enough to take them.

## Known limits of what is here

Worth stating plainly, because a trust-focused submission that oversells itself is self-defeating:

- **Conversation history is unbounded.** Every turn resends the full transcript, so cost and latency climb
  through a long chat. Needs trimming or rolling summarisation.
- **Retrieval has no relevance floor.** An off-topic question still returns eight chunks, the model, not
  the retriever, decides what is relevant. A reranker and a "no good match" signal would make the
  escalation trigger for "no source supports this" fire on evidence rather than on judgment.
- **Sessions and checkpoints live in memory.** No eviction, no TTL, and a restart drops every conversation.
  The app must run as a single process, that is fine for a demo and wrong for production.
- **Time arithmetic is delegated to the tools on purpose.** In testing, the model restated a derived clock
  time incorrectly while getting the elapsed figure right, so the tools now return both the raw timestamps
  and the deltas and the prompt forbids the model from computing either.

## What I intentionally left out

- **Real authentication.** Mocked as an account dropdown, as the brief permits. Real auth is a solved
  problem and would have displaced work on the parts being assessed.
- **Problem 1's dashboard.** Doing one problem properly beat doing two thinly. See above for the plan.
- **Model-written SQL.** An LLM emitting SQL against the customer data store is the one design that could
  defeat the access control this submission is built around.
- **Persistent conversation storage.** In-process checkpointing, a restart drops history. A one-line swap
  when it matters.
- **Streaming responses and a polished UI.** The interface shows tool activity inline, which is what the
  brief asked to surface, beyond that it is deliberately plain.
- **Multi-tenant admin tooling.** Out of scope for a customer-facing agent.
