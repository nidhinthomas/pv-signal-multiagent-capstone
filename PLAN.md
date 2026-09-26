# Plan: Multi-Agent Pharmacovigilance Signal Detection Capstone

## Context

The capstone rubric requires demonstrating CLAUDE.md, Skills, Hooks, Sub-agents, MCP, state/memory, guardrails, governance, human-in-the-loop, evaluation, observability, and traceability. This is a greenfield build (directory currently only has `BUSINESS_CASE.md`).

Decisions made with the user so far:
- Business use case: pharmacovigilance signal detection over **synthetic** AE report data (drug catalog, patient cohort, AE cases, with a few drug-event pairs deliberately injected above baseline so detection can be scored against a ground truth).
- Runtime orchestration: **LangGraph** (not the Claude Code CLI itself), because the live demo needs a **Streamlit** UI, and this VM reaches Claude models through **OpenRouter** rather than a direct Anthropic key.
- MCP is a real, minimal server (not skipped, not simulated).
- Timeline: multi-session, untimed. Build **one phase at a time**, tracked in a persisted `PROGRESS.md`.
- The plan itself is persisted to the repo as `PLAN.md` (this file), not just kept in the ephemeral plan-mode scratch file — so it survives across sessions same as the code does.

This changes how the rubric's Claude-Code-specific items (Sub-agents, Skills, Hooks, CLAUDE.md) get satisfied — they can't just be "the demo runs inside a CC session" anymore. The design below makes each one **genuinely load-bearing** rather than decorative: subagent `.md` files are parsed and actually drive the LangGraph nodes; Claude Code hooks and skills operate at the dev/ops layer (data gen, eval, guardrail checks during development), and a parallel runtime hook layer (Python wrappers around LangGraph nodes) does the same job at execution time, since CC hooks only fire inside CC CLI sessions and this app runs standalone.

## Critical review (devil's-advocate pass, and what changed because of it)

A deliberate pass looking for what would break or ring hollow in front of an audience, checked against this VM, not assumed:

1. **No real web-search tool exists in this runtime — confirmed, not assumed.** Checked: no `TAVILY_API_KEY`/`SERPER_API_KEY`/etc. and no search client package installed. `WebSearch` is a Claude Code-specific tool; a LangGraph node calling OpenRouter's chat-completions endpoint has no web access unless we build it ourselves. **Fix**: `literature-reviewer` searches a small **synthetic literature corpus** (`data/literature_corpus.json`, generated alongside the AE data) via an MCP tool, not live WebSearch. This also removes dependency #2 below.
2. **Real-vs-fake drug confusion risk.** A synthetic "signal" about a real drug name, screenshotted out of context, could be mistaken for an actual safety finding — and live web search against a real drug name would surface true side-effect info that gets tangled with our fake injected signal. **Fix**: the synthetic generator uses clearly fictional drug names (e.g. `Neuroclarin`, `Vastocor`) and a persistent `SYNTHETIC DEMO — NOT REAL DATA` banner in every report and every UI screen.
3. **Parallel fan-out with multiple simultaneous LangGraph interrupts is a reliability trap** for a live demo — matching the "right" resume to the right paused branch among several concurrent ones is fiddly. **Fix**: process candidate signals **sequentially**, one full cycle (literature review → report → human approval) at a time, not fanned out in parallel. Slower, but reliable, and better for the "watch the reasoning step by step" UX goal — parallel traces would interleave confusingly on screen anyway.
4. **Tautological evaluation risk** — if the same build controls both the injected-signal strength and the detection threshold, precision/recall can trivially read 100% without proving the detector does real work. **Fix**: the generator varies signal strength (a couple of strong/obvious pairs, at least one deliberately borderline pair near the threshold, plenty of pure-noise pairs at realistic co-occurrence rates) and includes one **trap case** — a pair that's statistically significant but corresponds to an event already listed on that drug's synthetic label — so `safety-report-writer`'s "don't overclaim novelty" guardrail and the human reviewer's judgment both have real work to do, not just rubber-stamping.
5. **Streamlit reruns the entire script on every interaction.** If the compiled LangGraph graph or the MCP client got re-created on every rerun, behavior would get flaky in ways that are hard to debug live. **Fix**: both are held via `st.cache_resource` as long-lived, once-per-server-process objects; only the sqlite-backed checkpoint state (keyed by `thread_id`) is expected to persist/reload across reruns.
6. **Minor efficiency/realism fix**: statistics belong in deterministic code, not in per-pair agent tool-call loops. The MCP server gets a `scan_signals(prr_threshold, min_cases)` tool that computes the full disproportionality table server-side in one call; `signal-detector`'s job is interpreting and prioritizing that table, not computing it pair-by-pair.
7. **Governance note added**: even though the data is synthetic, routing it through a third-party API gateway (OpenRouter) is itself a governance consideration that would need a BAA/data-processing agreement in a real deployment — called out explicitly in the Governance section as a limitation, not glossed over.
8. **No cross-run memory** — the original design only had within-a-run working state (the LangGraph `State`, checkpointed for pause/resume). Real pharmacovigilance is cumulative: a signal reviewed and dismissed last quarter shouldn't be re-alerted from scratch next quarter as if brand new, and a human reviewer's past decision should count for something. **Fix**: add an explicit long-term memory layer, separate from per-run state — see "Memory architecture" below.

## Memory architecture: short-term vs. long-term

Two distinct mechanisms, not one:

- **Short-term (working) memory** — the LangGraph `State` object for one run (`thread_id = run_id`): the candidate list, evidence gathered so far, the `trace`, the pending human decision. Scoped to a single run; durable only for the life of that run via the sqlite checkpointer (so it survives a Streamlit rerun or process restart mid-run, but has no meaning once the run finalizes).
- **Long-term (cross-run) memory** — a persistent store, `memory/signal_history.sqlite`, that survives across every run, independent of any single `thread_id`. For each drug-event pair it holds: first-detected run, latest PRR/case count, and a full review history (`[{run_id, decision, reviewer_note, timestamp}]`). Exposed via two new MCP tools:
  - `get_signal_history(drug_name, event_name)` — read-only, callable by `signal-detector` so it can annotate each candidate as new / previously reviewed-and-rejected / previously approved-and-still-monitored, and explain *why* it's surfacing something again (e.g. case count grew from 12 to 31 since the last rejection) instead of re-presenting stale signals as fresh news.
  - `record_signal_decision(...)` — write-only, callable **only from the `finalize` node, only after a human decision has been recorded** — never by `signal-detector`, `literature-reviewer`, or `safety-report-writer`. This is enforced both by tool allowlisting (those three subagent specs simply don't list it) and by the Phase 5 guardrail hook (reject any call to it that isn't tagged as coming from `finalize` post-approval).
- This also gives Phase 8's evaluation something concrete to test: run the pipeline twice over data that includes one previously-rejected pair, and confirm the second run's report correctly says "previously reviewed and rejected" rather than presenting it as a new finding.

## Confirmed environment (checked, not assumed)

- Python 3.14.4 at `/home/labuser/venv/bin/python3`.
- Already installed: `langgraph` 1.2.11, `langgraph-checkpoint-sqlite`, `langchain-openai`, `langchain-core`, `mcp` 2.2.0, `streamlit` 1.63.0, `openai`, `anthropic`. No new heavy dependencies needed.
- Env vars present: `OPENROUTER_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`.
- Mechanics: LLM calls go through `langchain_openai.ChatOpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"], model="anthropic/...")`. **The exact OpenRouter model slug for Claude Sonnet 5 must be confirmed at build time** (query OpenRouter's `/models` endpoint) rather than guessed.
- `langgraph-checkpoint-sqlite` is what makes human-in-the-loop pause/resume work correctly across separate Streamlit reruns/processes (via `thread_id`-keyed checkpoints).

## Directory Structure (target end state)

```
capstone/
  PLAN.md                          # this plan, persisted (written in Phase 0)
  PROGRESS.md                      # phase-by-phase tracker, updated after every phase
  CLAUDE.md                        # business rules, thresholds, guardrail language, architecture
  BUSINESS_CASE.md                 # (exists)
  README.md                        # architecture diagram, governance, how to run (Phase 10)
  requirements.txt
  config.py                        # shared constants: PRR threshold, min case count, model slug, allowed data dirs

  data/
    generate_synthetic_data.py     # fictional drug/event names; varied signal strength; one trap case
    db.sqlite                      # generated
    ground_truth_signals.json      # generated: includes signal strength label + the trap case flag
    literature_corpus.json         # generated: small synthetic corpus literature-reviewer searches (replaces live WebSearch)

  mcp_server/server.py             # query_ae_reports, scan_signals, calculate_prr, get_drug_label, search_literature, get_signal_history, record_signal_decision
  mcp_client/client.py             # stdio client wrapper for LangGraph nodes

  memory/
    signal_history.sqlite          # long-term, cross-run memory: persists across every run (NOT regenerated by generate_synthetic_data.py)

  agents/
    loader.py                      # parses .claude/agents/*.md into node config
    graph.py                       # LangGraph StateGraph + checkpointer + interrupt()
    hooks.py                       # runtime guardrail + observability wrappers
    state.py                       # shared State schema (incl. trace: list[StepRecord] for step-by-step reasoning) + run persistence to runs/<id>/state.json

  .claude/
    agents/
      signal-detector.md
      literature-reviewer.md
      safety-report-writer.md
    skills/
      pv-generate-data/SKILL.md
      pv-evaluate/SKILL.md
    settings.json                  # Claude Code hooks (dev/ops layer)
    hooks/
      log_tool_use.py
      validate_report.py

  app.py                           # Streamlit front-end

  runs/<run_id>/state.json
  reports/<run_id>-safety-report.md
  logs/observability.jsonl
  eval/evaluate.py
  eval/eval_results.md             # generated
```

## Execution strategy to fit ~3.5-4 hours without cutting scope

The phase list below still defines *what* gets built — nothing is being dropped (memory layer, trap case, varied signal strength, CC dev/ops layer, and eval depth all stay). What changes is *how* the phases are sequenced, to compress wall-clock:

1. **De-risk the scariest unknown first, cheaply.** Before building the real pipeline, a 15-minute throwaway spike script confirms three things that Phase 4 depends on and that are the most likely source of late, expensive surprises: the exact OpenRouter model slug works, `ChatOpenAI(...).stream(...)` actually streams token-by-token through OpenRouter, and a minimal one-node LangGraph graph with `interrupt()` + `SqliteSaver` actually pauses and resumes. This folds into Phase 0 rather than being discovered mid-Phase-4.
2. **Fix the UI design decisions once, upfront, instead of iterating live.** The color palette (per-agent accent colors + semantic status colors), tab layout, and component choices for the "attractive/good UX" requirement get decided and written down as a short design spec in `ARCHITECTURE.md` during Phase 0 (informed by the `dataviz` skill for the palette). Phase 6 then becomes pure implementation against a fixed spec, not exploratory design-and-redesign — that back-and-forth is what usually makes UI work slow.
3. **Parallelize independent phases using background subagents.** Phases 1 (synthetic data), 2 (MCP server), 3's agent-spec files, and 7 (CC dev/ops skills/hooks) don't depend on each other's *implementations* — only on interfaces already pinned down in this plan (tool names/signatures, drug/event field names). Once those interfaces are fixed in Phase 0, these are dispatched as parallel Agent tool calls rather than built one at a time; wall-clock for that block becomes roughly the length of its slowest single piece (the MCP server), not the sum of all of them.
4. **Keep the true critical path short.** After the parallel block, the unavoidably sequential part is: Phase 4 (graph + memory wiring, de-risked by the Phase 0 spike) → Phase 5 (hooks) → Phase 6 (UI, implementation-only per point 2) → Phase 8 (eval) → Phase 9 (docs, largely assembled from `ARCHITECTURE.md`/`PLAN.md` content already written rather than drafted fresh).

Rough revised total: spike + design spec (~30 min) + parallel block (~45-60 min wall-clock) + graph (~60 min, de-risked) + hooks (~20 min) + UI (~45-60 min, implementation-only) + eval (~30 min) + docs (~20 min) ≈ **3.5-4.5 hours**. This is a realistic compression, not a promise — the main residual risk is still Phase 4 (LangGraph interrupt/checkpointer + OpenRouter streaming behaving unexpectedly under the real multi-node graph even after the spike passes), so that phase keeps its own time buffer and we flag early if it's running long rather than silently absorbing the overrun into later phases.

## Git workflow

- Repo: a **new public GitHub repo** under the authenticated account (`nidhinthomas`, confirmed via `gh auth status`), created in Phase 0 via `gh repo create` and set as `origin`. Proposed name: **`pv-signal-multiagent-capstone`** — say so now if a different name is wanted before Phase 0 runs, otherwise this is what gets created.
- Every phase ends with a commit (already planned as "commit-sized chunk of work" below) **and a push to `origin`** right after that phase's exit check passes — not batched at the end. Commit messages reference the phase (e.g. `"Phase 2: MCP server (scan_signals, calculate_prr, get_signal_history, ...)"`).
- `.gitignore` created in Phase 0 covering generated artifacts that shouldn't be committed as if they were source: `data/db.sqlite`, `memory/signal_history.sqlite`, `runs/`, `logs/`, `reports/`, `eval/eval_results.md`, Python venv/cache — the *generators* for these are committed, their output is not.

## Phases

Each phase ends with: a working, testable artifact; a commit-sized chunk of work; a **push to GitHub** (see Git workflow above); and a `PROGRESS.md` update (status, date, what was verified, any deviations from this plan). Phases 1, 2, 3 (agent-spec files only), and 7 are dispatched in parallel per the execution strategy above; the rest are sequential.

### Phase 0 — Scaffolding, Docs, Design Spec, Smoke Test & Repo Setup
- Create `PLAN.md` (this document), `PROGRESS.md` (phase tracker table), `ASSIGNMENT.md` (the capstone brief/rubric pasted verbatim, kept as a fixed reference to check the build against), `ARCHITECTURE.md` (the authoritative technical reference we build against — component diagram in mermaid, control/data flow through the LangGraph pipeline, the directory structure and component design sections currently in this plan, tech stack and why each piece was chosen, **plus the fixed UI design spec**: palette, per-agent accent colors, semantic status colors, tab layout), `requirements.txt` (pin the already-installed versions), `config.py` stub, `.gitignore`, directory skeleton.
- **Smoke-test spike** (throwaway script, not part of the final codebase): confirm the OpenRouter model slug, confirm `ChatOpenAI(...).stream(...)` streams incrementally through OpenRouter, confirm a minimal single-node LangGraph graph with `interrupt()` + `SqliteSaver` pauses and resumes correctly. This is what de-risks Phase 4.
- `git init`, `gh repo create pv-signal-multiagent-capstone --public --source=. --remote=origin`, initial commit + push.
- `ARCHITECTURE.md` is a living document: if a later phase discovers the real implementation has to diverge from what's written here, `ARCHITECTURE.md` gets updated at that point (and the divergence noted in `PROGRESS.md`'s log) — it should always reflect what's actually true, not just what was originally planned. `README.md` (Phase 9) will be the separate how-to-run/governance/rubric-mapping doc and will link to this one rather than duplicating it.
- **Exit check**: `PROGRESS.md` exists with all phases listed as "Not started"; `PLAN.md` matches this plan; `ASSIGNMENT.md` matches the brief the user provided; `ARCHITECTURE.md` contains a diagram, the UI design spec, and matches the Directory Structure / Component Design sections below; the smoke-test spike passes all three checks above; the GitHub repo exists with the initial commit pushed.

### Phase 1 — Synthetic Data
- `data/generate_synthetic_data.py`: drug catalog (fictional names), patient cohort, AE cases → `db.sqlite`, with enough volume (thousands of reports) for PRR to be statistically meaningful rather than noisy. Inject signals at **varied strength**: 2-3 strong/obvious pairs, at least 1 deliberately borderline pair near the detection threshold, and 1 **trap case** (statistically elevated but already listed on that drug's synthetic label — i.e. not a novel signal). Plenty of pure-noise pairs at realistic background co-occurrence rates.
- `data/literature_corpus.json`: a small synthetic corpus of fictional "publications" — some entries corroborate specific injected pairs, some contradict, most are irrelevant noise. This is what `literature-reviewer` will search instead of live WebSearch.
- Write the answer key to `ground_truth_signals.json`, including each pair's intended strength category and whether it's the trap case.
- **Exit check**: run the generator; inspect row counts and injected-pair PRR values look plausible (strong pairs clearly elevated, the borderline pair genuinely close to the threshold, the trap case statistically real but label-known) before moving on.

### Phase 2 — MCP Server & Client
- `mcp_server/server.py`: `query_ae_reports`, `scan_signals(prr_threshold, min_cases)` (computes the full disproportionality table server-side in one call — deterministic stats, not agent-looped math), `calculate_prr` (on-demand single-pair lookup), `get_drug_label`, `search_literature(query)` (keyword search over `literature_corpus.json`), `get_signal_history(drug_name, event_name)` (reads `memory/signal_history.sqlite`, read-only), `record_signal_decision(drug_name, event_name, run_id, decision, reviewer_note)` (writes to `memory/signal_history.sqlite` — intended caller: `finalize` node only, post-approval; enforced in Phase 5, not here).
- `mcp_client/client.py`: stdio client wrapper exposing plain async functions.
- **Exit check**: call all tools directly through the client wrapper (a throwaway script) and confirm correct results against known values from Phase 1's data — including that `scan_signals` actually recovers the strong pairs, is borderline on the borderline pair, and flags the trap case as statistically significant (its "already known" status is a separate check, not something `scan_signals` itself decides); and that a `record_signal_decision` write followed by `get_signal_history` on the same pair round-trips correctly — before any agent touches them.

### Phase 3 — Agent Specs, Model Wiring & Shared State
- Confirm the exact OpenRouter model slug (query `/models`), record it in `config.py`.
- `.claude/agents/signal-detector.md`, `literature-reviewer.md`, `safety-report-writer.md` (standard CC subagent frontmatter + system prompt). Tool allowlists, least-privilege: `signal-detector` gets `scan_signals`, `calculate_prr`, `get_signal_history` (read-only long-term memory lookup — never `record_signal_decision`); `literature-reviewer` gets `search_literature` + `get_drug_label` — **no WebSearch** (confirmed unavailable in this runtime; see Critical review); `safety-report-writer` gets no tools at all, only the accumulated state. `record_signal_decision` is not on any subagent's allowlist — only the orchestrator's `finalize` node calls it, after the human decision is recorded.
- `agents/loader.py` (parses those files into node config), `agents/state.py` (State schema + `runs/<id>/state.json` persistence).
- **Exit check**: loader correctly parses each `.md` file's frontmatter/system prompt/tool allowlist in a quick unit test.

### Phase 4 — LangGraph Pipeline (CLI-only, no UI yet)
- `agents/graph.py`: `signal_detector` (calls `scan_signals` once, then `get_signal_history` per candidate to annotate new/previously-reviewed/previously-rejected-now-recurring) → then **sequentially**, one candidate at a time: `literature_reviewer` → `safety_report_writer` → `interrupt()` for human approval → `finalize` (records the decision via `record_signal_decision`, the only place in the whole system allowed to write to long-term memory) → next candidate → ... Deliberately **not** fanned out in parallel (see Critical review #3) — one candidate's full cycle, including its own approval gate, completes before the next starts. Uses the sqlite checkpointer keyed by `thread_id=run_id` for short-term/working state; `memory/signal_history.sqlite` (Phase 2) is the separate long-term store this reads from and writes to.
- Every generated report/state record includes the `SYNTHETIC DEMO — NOT REAL DATA` banner text as a field, not just a UI overlay, so it travels with the data itself.
- **Step-by-step reasoning capture** (needed by Phase 6's UI): each node's system prompt requires it to think step-by-step before its final structured output, in a clearly delimited `### Reasoning` / `### Conclusion` format (Claude via OpenRouter may or may not surface a separate `reasoning` field depending on the model — don't assume it does; the delimited-prompt approach is the reliable fallback). `agents/state.py`'s `State` gets a `trace: list[StepRecord]` field, where each `StepRecord` = `{agent, step_type: "reasoning"|"tool_call"|"tool_result"|"output", content, timestamp}`. Every node appends its reasoning text, each tool call + result, and its final output as separate ordered records — this is the raw material for both the UI's reasoning view and the Traceability/Observability rubric items.
- Nodes invoke the LLM with streaming (`llm.stream(...)` / LangGraph `stream_mode="messages"`) so reasoning text is available incrementally, not just after the fact — needed for Phase 6's live view.
- A throwaway CLI test script to run the graph end-to-end, feeding the approval decision back in via `Command(resume=...)`.
- **Exit check**: a full run completes via CLI script only, pausing correctly at each candidate's interrupt and resuming correctly after a simulated process restart (kill and re-run the script against the same `thread_id`) — this proves the checkpointer is actually doing its job. Confirm the trap case's report correctly flags it as "already known, not a novel signal" (proves the guardrail against overclaiming novelty is real). Also print the captured `trace` list and confirm it contains a legible reasoning step for every node, not just final outputs.

### Phase 5 — Runtime Hooks (Guardrails + Observability)
- `agents/hooks.py`: pre-call guardrail checks (block reads outside `data/db.sqlite`; block report writes missing disclaimer/approval field/banner; **block any call to `record_signal_decision` that didn't originate from the `finalize` node after a recorded human decision**) and post-call observability logging to `logs/observability.jsonl`. Wire into `graph.py`.
- **Exit check**: deliberately trigger each guardrail violation — strip a report's disclaimer, and separately try calling `record_signal_decision` from a non-`finalize` context — and confirm both are blocked; confirm a normal run produces a complete `observability.jsonl` trail.

### Phase 6 — Streamlit UI
- `app.py` layout: a persistent `SYNTHETIC DEMO — NOT REAL DATA` banner at the top of every screen; a sidebar for run controls/run history; a main area with tabs — **Live Run** (step-by-step reasoning view, one candidate at a time), **Draft Report** (with approval actions), **Evaluation** (metrics), **Observability** (raw log/trace browser).
- **Lifecycle correctness**: the compiled LangGraph graph and the MCP client session are both held in `st.cache_resource` (constructed once per server process), not rebuilt on every rerun — Streamlit reruns the whole script on every click, so anything not cached this way would silently reset. Only the sqlite-backed checkpoint (keyed by `thread_id`) is expected to carry state across reruns.
- **Live Run / reasoning view**: streams the `trace` records from Phase 4 as they're produced (via `graph.stream(...)`) into an expandable, chronological timeline — one card per step, clearly labeled by agent and step type (reasoning / tool call / tool result / final output), so a reviewer can watch *why* the signal-detector flagged a pair, *what* the literature-reviewer searched for and found, and *how* the report was drafted — not just see the end result. This is the same `trace` data that feeds Observability/Traceability, rendered for humans instead of just logged.
- **Draft Report tab**: the rendered report plus Approve / Reject / Send-back-for-more-evidence buttons, wired to `Command(resume=...)` against the correct `thread_id`.
- **Visual design**: a single deliberate, accessible color palette used consistently across the whole app — e.g. one consistent accent color per agent role (signal-detector / literature-reviewer / safety-report-writer / human-approval) reused for that agent's badges, timeline cards, and any charts about it, plus fixed semantic colors for Approved/Rejected/Pending status (not ad hoc per-widget colors). Any charts (e.g. PRR scores per candidate, precision/recall in the Evaluation tab) should follow the **dataviz** skill's palette and mark guidance so they're consistent with each other and readable in light/dark. Prioritize clear visual hierarchy (run controls always visible, current stage obvious at a glance, draft report never buried) over dense information display.
- **Exit check**: run one case to Approve and one to Reject through the actual UI; confirm both outcomes land correctly in `runs/<id>/state.json`; confirm the Live Run tab shows a distinguishable reasoning step for every agent in the pipeline, live (not only after the run finishes).

### Phase 7 — Dev/Ops Claude Code Layer
- `.claude/skills/pv-generate-data/SKILL.md`, `.claude/skills/pv-evaluate/SKILL.md`, `.claude/settings.json`, `.claude/hooks/log_tool_use.py`, `.claude/hooks/validate_report.py`.
- **Exit check**: run `/pv-generate-data` and `/pv-evaluate` from Claude Code CLI and confirm the CC hooks actually fire (check their log output).

### Phase 8 — Evaluation
- `eval/evaluate.py`: precision/recall/F1 of all flagged signals (from `runs/*/state.json`) vs. `ground_truth_signals.json`, broken out by signal-strength category (strong/borderline/noise) so the numbers show the detector doing real work rather than a flat 100% — and a specific check that the trap case was flagged as statistically significant but correctly reported as "not novel." Rule-based report-quality checks (evidence cited? no causal language? approval field present? banner present?). Plus a **cross-run memory check**: run the pipeline twice over data containing one previously-rejected pair, and confirm the second run's report says "previously reviewed and rejected" rather than presenting it as new — this is what actually exercises the long-term memory layer, not just the per-run state.
- **Exit check**: run against accumulated test runs from Phases 4/6; sanity-check the numbers, especially that the borderline pair's detection isn't trivially certain, the trap case is handled correctly, and the two-run memory check passes.

### Phase 9 — Documentation & Governance
- `README.md`: governance section (data privacy, access control, accountability, and the explicit limitation that routing data through a third-party API gateway (OpenRouter) would itself require a BAA/data-processing agreement in a real deployment even though this demo's data is synthetic), limitations, how to run the demo end-to-end, and a link to `ARCHITECTURE.md` rather than a duplicated diagram.
- Final pass over `ARCHITECTURE.md` and `CLAUDE.md` to make sure both match what was actually built (not just what Phase 0 originally planned).
- Full rubric-compliance checklist (see "Periodic rubric checks" below) written into `README.md`.
- **Exit check**: a fresh reader (or fresh Claude Code session) could follow `README.md` + `ARCHITECTURE.md` alone to understand and run the full demo.

## Periodic rubric checks

`ASSIGNMENT.md` (Phase 0) is the fixed reference; we check the build against it more than once, not only at the very end:
- After Phase 4 (pipeline works end-to-end via CLI) — sanity-check that Sub-agents, MCP, State/Memory, and Human-in-the-Loop are genuinely present, since those are the components most likely to get quietly lost when wiring LangGraph.
- After Phase 7 (dev/ops CC layer done) — check CLAUDE.md, Skills, Hooks, Guardrails, Governance are all actually implemented, not just planned.
- Phase 9 — full line-by-line pass: every mandatory component in `ASSIGNMENT.md` mapped to the specific file/phase that satisfies it, written up as a checklist in `README.md`. Anything that doesn't map cleanly gets flagged and fixed before calling the capstone done.
- Each of these checks gets a log entry in `PROGRESS.md` (see format below), so drift is caught and recorded, not just silently corrected.

## PROGRESS.md format (created in Phase 0, updated every phase)

A table plus a running log:

```markdown
# Progress

| Phase | Status | Date | Notes |
|---|---|---|---|
| 0 - Scaffolding & Docs | Not started | | |
| 1 - Synthetic Data | Not started | | |
| 2 - MCP Server & Client | Not started | | |
| 3 - Agent Specs & State | Not started | | |
| 4 - LangGraph Pipeline | Not started | | |
| 5 - Runtime Hooks | Not started | | |
| 6 - Streamlit UI | Not started | | |
| 7 - Dev/Ops CC Layer | Not started | | |
| 8 - Evaluation | Not started | | |
| 9 - Docs & Governance | Not started | | |

## Log
(reverse-chronological entries: what was done, what was verified, any deviations from PLAN.md)
```

## Verification (cumulative, referenced by each phase's exit check above)

- MCP tools verified standalone before agents use them, including that `scan_signals` correctly separates strong/borderline/noise pairs (Phase 2).
- LangGraph pipeline verified via CLI before the UI is added (Phase 4), including a real interrupt/resume across a process restart, sequential (not parallel) per-candidate processing, and correct handling of the trap case.
- Guardrail hook verified with a deliberate negative case, not just the happy path (Phase 5).
- Both Approve and Reject paths exercised through the actual UI, with the compiled graph/MCP client confirmed to survive reruns via `st.cache_resource` (Phase 6).
- Evaluation run against real accumulated data, not fabricated numbers, broken out by signal strength (Phase 8).
