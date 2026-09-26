# CLAUDE.md — Project Instructions

Project: multi-agent pharmacovigilance signal-detection capstone (synthetic data only). This file defines project instructions, architecture pointers, coding standards, and business rules/constraints for any Claude Code session (interactive dev work or the `.claude/skills` dev/ops layer) working in this repo. It is **not** consulted by the runtime pipeline itself — the runtime's agent behavior is defined by `.claude/agents/*.md` and enforced by `agents/hooks.py`; this file governs how the codebase is built and maintained.

## What this project is

A LangGraph-orchestrated pipeline (Streamlit front-end, Claude via OpenRouter, a real MCP server) that detects candidate drug-event safety signals in **synthetic** adverse-event report data, gathers literature context, drafts a reviewer-ready report, and requires human approval before any finding is recorded to long-term memory. See `BUSINESS_CASE.md` for the business rationale, `ARCHITECTURE.md` for the technical design, `PLAN.md` for the phased build plan, `ASSIGNMENT.md` for the fixed rubric this is checked against.

## Non-negotiable business rules and constraints

1. **Data is synthetic, always.** No real patient data, no real FAERS data, no real drug names presented as real. Every generated report and every UI screen carries the `SYNTHETIC DEMO — NOT REAL DATA` banner (`config.SYNTHETIC_DATA_BANNER`) as a literal field, not just a UI overlay.
2. **No live web search.** This runtime (LangGraph nodes calling OpenRouter) has no web-search tool available — confirmed by environment check, not assumed. `literature-reviewer` searches only the synthetic `data/literature_corpus.json` via the `search_literature` MCP tool. Do not add a WebSearch/Tavily/Serper dependency without re-confirming this constraint no longer holds.
3. **Statistics are deterministic, not agent-computed.** PRR / disproportionality analysis happens server-side in `mcp_server/server.py`'s `scan_signals`/`calculate_prr` tools. Agents interpret and prioritize results; they do not compute statistics themselves.
4. **Long-term memory writes are single-gated.** `record_signal_decision` may only be called by the `finalize` graph node, only after a human decision has been recorded. It must never appear in any subagent's tool allowlist. This is enforced twice — allowlisting (Phase 3) and the runtime guardrail hook (Phase 5) — and both enforcement points must be kept in sync if the tool is ever renamed or moved.
5. **Human approval is mandatory and cannot be bypassed.** Every candidate signal pauses at `interrupt()` before finalization. No code path may call `record_signal_decision` without a real human decision having been captured first.
6. **Sequential, not parallel, candidate processing.** Do not fan out multiple candidates' literature-review/report/approval cycles concurrently — this is a deliberate reliability choice (see `PLAN.md` Critical Review #3), not an oversight to "optimize" later.
7. **Least privilege on tool allowlists.** `signal-detector`: `scan_signals`, `calculate_prr`, `get_signal_history` only. `literature-reviewer`: `search_literature`, `get_drug_label` only. `safety-report-writer`: no tools. Do not widen these without updating `ARCHITECTURE.md` §5.1 and noting why in the Divergence Log.

## Coding standards

- Python, targeting the existing venv at `/home/labuser/venv/bin/python3`. Use `config.py` constants (paths, thresholds, model slug, banner text) rather than re-declaring them locally.
- MCP tools are called through `mcp_client/client.py`'s stdio wrapper — never import `mcp_server/server.py` functions directly from agent/graph code. The protocol must stay genuinely in the path.
- Streamlit: anything expensive or stateful across reruns (the compiled LangGraph graph, the MCP client session) must be constructed via `st.cache_resource`, never at module top-level or rebuilt per-rerun.
- New LLM-facing nodes must prompt for a delimited `### Reasoning` / `### Conclusion` structure and append every reasoning chunk, tool call, tool result, and final output to `state.trace` as a `StepRecord` — this is what feeds both the Live Run UI and the Observability/Traceability rubric items. Don't skip trace population "to save time" on a new node.
- Keep `ARCHITECTURE.md` in sync with what's actually built. If an implementation has to diverge from the architecture doc, update the doc and log the divergence in its §9 Divergence Log — don't let the doc silently go stale.

## Working process for this repo

- Build one phase at a time per `PLAN.md`. Update `PROGRESS.md` (status + date + notes, plus the running log) at the end of every phase, before moving to the next.
- Every phase ends with a commit and a push to `origin` right after its exit check passes (see `PLAN.md` → Git workflow) — not batched.
- Periodically check the build against `ASSIGNMENT.md` (after Phase 4, after Phase 7, full mapping in Phase 9) and log the check in `PROGRESS.md`.
