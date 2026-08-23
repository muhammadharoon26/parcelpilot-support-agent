# AI Tool Usage

I used **Claude Code** (Anthropic's CLI agent) throughout this build.

**How I used it.** I wrote a technical design document first — stack, directory layout, access-control
rules, the authority ranking, the LangGraph node/edge structure, and an explicit "what to leave out"
section — and had Claude Code implement against that spec rather than prompting it feature by feature.
Deciding the architecture up front is what kept the result small; the design doc is in the repo root as
`ParcelPilot_Technical_Design_Doc.md`.

Claude Code did the mechanical work: reading the six PDFs and the workbook to derive the data model,
scaffolding the FastAPI service and the LangGraph graph, writing the chat UI, and writing the test suite.
It also caught the protocol constraint that a turn mixing a lookup with an escalation must return both
tool results in one message, which is why the graph carries a `results_buffer`.

**What I decided myself.** The source authority ranking and which document sits at which rank. Splitting
facts from rules so that policy thresholds live in retrievable documents rather than in SQL. Holding the
action tool behind `interrupt()` and a UI card instead of a chat confirmation. Removing `account_id` from
the tool schemas entirely rather than validating it after the fact. The escalation triggers. And the call
to swap the model provider to Gemini when an Anthropic key wasn't available, which is why `llm.py` exists.

**What I checked by hand.** Every access-control claim, by running the cross-account cases directly rather
than trusting that the code looked right. The trap records in the pack (TKT-450 and TKT-451, whose
historical resolutions contradict the governing sources) were the specific cases I used to test whether
the reliability logic actually worked end to end.
