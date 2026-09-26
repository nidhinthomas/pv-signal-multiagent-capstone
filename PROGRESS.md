# Progress

| Phase | Status | Date | Notes |
|---|---|---|---|
| 0 - Scaffolding, Docs, Design Spec, Smoke Test & Repo Setup | Done | 2026-09-26 | Repo created + pushed: github.com/nidhinthomas/pv-signal-multiagent-capstone |
| 1 - Synthetic Data | Done | 2026-09-26 | 10,681 AE reports; 2 strong + 1 borderline + 1 trap-case signal, verified via independent SQL |
| 2 - MCP Server & Client | Done | 2026-09-26 | 7 tools built (parallel agent); self-test (33 checks) + live integration check against real Phase 1 data both pass |
| 3 - Agent Specs, Model Wiring & Shared State | Done | 2026-09-26 | 3 subagent specs (parallel agent) + loader.py/state.py (sequential); loader unit test passed |
| 4 - LangGraph Pipeline (CLI-only) | Not started | | |
| 5 - Runtime Hooks (Guardrails + Observability) | Not started | | |
| 6 - Streamlit UI | Not started | | |
| 7 - Dev/Ops Claude Code Layer | Done | 2026-09-26 | 2 skills + settings.json + 2 hooks (parallel agent); self-tests + live hook fire/block both confirmed |
| 8 - Evaluation | Not started | | |
| 9 - Documentation & Governance | Not started | | |

## Log

(reverse-chronological — newest entries at the top)

### 2026-09-26 — Phase 7 complete (built by parallel background agent), plus incident note
- `.claude/skills/pv-generate-data/SKILL.md`, `.claude/skills/pv-evaluate/SKILL.md`: run the data generator / evaluator via the venv interpreter, verify expected outputs exist, and report the specific numbers/checks (signal-strength breakdown, trap-case check, report-quality checks, cross-run memory check) rather than fabricating a summary.
- `.claude/settings.json` wires `PreToolUse` (all tools) → `.claude/hooks/log_tool_use.py` (dev/ops tool-use logging to `logs/cc_dev_tool_use.jsonl`, fails open on any error) and `PostToolUse` (Write|Edit) → `.claude/hooks/validate_report.py` (blocks — exit 2 — a `reports/*.md` write/edit missing the synthetic banner, a review-status/approval marker, or containing unhedged causal language).
- **Incident**: the agent's first attempt wrote `settings.json` before its referenced hook script existed, which — since Claude Code re-evaluates `PreToolUse` hooks live against every tool call in the project, fail-closed on a hook error — froze every tool call in both that agent's and this session's own tool use (nothing in-session could fix a hook blocking the very tools needed to fix it). Resolved only by the user deleting `.claude/settings.json` from outside the blocked session. On retry, the agent verified each hook script with `py_compile` + direct stdin execution *before* wiring `settings.json`, avoiding a repeat.
- Exit check passed: hook self-tests (fail-open on malformed input; correct pass/fail detection) plus a live end-to-end check — a real tool call confirmed `PreToolUse` logging fires, and a real `Write` of a deliberately deficient report file correctly triggered a live blocking `PostToolUse` error with the exact missing items named.
- Committed and pushed.

### 2026-09-26 — Phase 2 complete (built by parallel background agent)
- `mcp_server/server.py`: all 7 tools from `ARCHITECTURE.md` §6.1 (`query_ae_reports`, `scan_signals`, `calculate_prr`, `get_drug_label`, `search_literature`, `get_signal_history`, `record_signal_decision`), exact signatures/schemas. PRR computed deterministically server-side via one grouped SQL query, not per-pair. `record_signal_decision` has no caller restriction at this layer — that gating is Phase 5's job.
- `mcp_client/client.py`: async stdio wrapper (`MCPClient` + module-level plain-async functions per tool), spawning `server.py` as a real subprocess — never imported directly.
- `mcp_server/_test_tools.py`: a self-contained unit test (throwaway sqlite + literature fixtures, hand-derived PRR expectations) exercised only through the client wrapper. Kept (not deleted) as a real regression test, not treated as throwaway scratch work.
- Exit check passed twice: (1) `_test_tools.py`, 33/33 checks pass against the fixture; (2) live integration check against Phase 1's real `data/db.sqlite` — `scan_signals` correctly recovers both strong pairs, the borderline pair, and the trap case (matching Phase 1's independently-verified PRR values exactly) while correctly excluding all 4 noise pairs.
- Committed and pushed.

### 2026-09-26 — Phase 3 complete
- Agent-spec files (`.claude/agents/signal-detector.md`, `literature-reviewer.md`, `safety-report-writer.md`) built by a parallel background agent: least-privilege tool allowlists exactly per `ARCHITECTURE.md` §5.1, delimited `### Reasoning`/`### Conclusion` output structure in all three, honest "no history"/"no literature found" instructions (no fabrication), and safety-report-writer's hard guardrail against overclaiming novelty for label-known events.
- `agents/loader.py` (sequential, built directly): parses `.claude/agents/*.md` frontmatter (`name`, `description`, `tools` — handles both YAML-list and comma-separated string forms) + system-prompt body into an `AgentSpec`. `agents/state.py`: `PipelineState` TypedDict (candidates, current literature/report, `trace: list[StepRecord]`, finalized decisions) + JSON snapshot to `runs/<run_id>/state.json`.
- Exit check passed: unit test confirms all three spec files parse with the exact expected tool allowlists and that the delimited reasoning/conclusion markers are present; `state.py` save/load round-trips correctly.
- `pyyaml` (already installed) added to `requirements.txt`, since `loader.py` needs it.
- Committed across 2 commits (subagent specs, then loader/state) and pushed.

### 2026-09-26 — Phase 1 complete (built by parallel background agent)
- `data/generate_synthetic_data.py`: 10 fictional drugs x 18 fictional/generic AE terms, seed=42, closed-form solve for exact target PRR per engineered pair (not pure random simulation) so every target lands in its acceptance band deterministically.
- Generated `data/db.sqlite` (10,681 `ae_reports` rows, 10 `drugs`), `data/ground_truth_signals.json`, `data/literature_corpus.json` (26 publications: 7 corroborates, 2 contradicts, 17 irrelevant) — all gitignored per plan, only the generator script is committed.
- Engineered signals: 2 strong pairs (Neuroclarin/Hepatic Enzyme Elevation PRR 6.22, Vastocor/Tendon Rupture PRR 5.09), 1 borderline pair (Ferinox/Photosensitivity Rash PRR 2.14), 1 trap case (Cardiozan/QT Interval Prolongation PRR 4.51, already on Cardiozan's synthetic label).
- Exit check passed: independent SQL recomputation (not the generator's own printout) of every ground-truth pair's PRR/case_count, plus a full drug x event scan confirming zero unintended pairs cross `PRR_THRESHOLD=2.0` at `MIN_CASE_COUNT=3`.
- No deviations from `ARCHITECTURE.md` §6.1's schema.
- Committed (`f9799f8`) and pushed.

### 2026-09-26 — Phase 0 complete
- Plan approved by user; `PLAN.md` (persisted copy of the approved plan) and `ASSIGNMENT.md` (verbatim brief, pulled from the pre-compaction transcript) written.
- Directory skeleton created: `data/`, `mcp_server/`, `mcp_client/`, `memory/`, `agents/`, `.claude/agents/`, `.claude/skills/pv-generate-data/`, `.claude/skills/pv-evaluate/`, `.claude/hooks/`, `runs/`, `reports/`, `logs/`, `eval/`.
- `ARCHITECTURE.md` written: mermaid component diagram, control/data flow, directory structure, component design (subagents, MCP tools, two-tier memory, guardrails/observability, HITL), tech stack rationale, and the fixed UI design spec (palette, per-agent accents, semantic status colors, tab layout).
- `CLAUDE.md` written (not explicitly phase-tagged in `PLAN.md`'s phase list, but listed in the target directory structure and needed early since Claude Code reads it for all subsequent dev work) — business rules, guardrail language, coding standards.
- `requirements.txt` pinned to actually-installed versions (checked via `pip list`, not assumed). `config.py` stub with shared constants (PRR threshold 2.0, min case count 3, paths, `OPENROUTER_MODEL_SLUG`, synthetic-data banner text).
- `.gitignore` covering generated artifacts; `.gitkeep` placeholders added for gitignored-but-needed directories (`memory/`, `runs/`, `reports/`, `logs/`).
- **Smoke-test spike** (`_smoke_test_spike.py`) run and passed all three checks:
  1. Queried OpenRouter's `/models` endpoint directly — confirmed exact slug `anthropic/claude-sonnet-5` exists (not guessed).
  2. `ChatOpenAI(...).stream(...)` against that slug returned 2 incremental chunks on a trivial prompt — streaming confirmed genuinely incremental through OpenRouter.
  3. A minimal one-node LangGraph graph with `interrupt()` + `SqliteSaver`, run against a checkpointer opened in one `with` block then a *fresh* connection opened in a second `with` block (simulating a process restart) — paused correctly, then resumed correctly via `Command(resume=...)` against the same `thread_id`.
  - `config.OPENROUTER_MODEL_SLUG` updated from the originally-guessed `anthropic/claude-sonnet-4.5` to the confirmed `anthropic/claude-sonnet-5`.
- `git init`, branch renamed to `main`, git identity confirmed (Nidhin Thomas), `gh auth status` confirmed logged in as `nidhinthomas`.
- Initial commit created (14 files) and pushed. `gh repo create pv-signal-multiagent-capstone --public --source=. --remote=origin` succeeded — repo live at https://github.com/nidhinthomas/pv-signal-multiagent-capstone.
- **Deviation from plan**: none of substance. The only adjustment was discovering the model slug should be `anthropic/claude-sonnet-5` rather than the `anthropic/claude-sonnet-4.5` placeholder mentioned in earlier planning — expected, since the plan always said this must be confirmed at build time, not guessed.
- Next: Phase 1 (Synthetic Data), Phase 2 (MCP Server & Client), Phase 3's agent-spec files, and Phase 7 (Dev/Ops CC Layer) — dispatched in parallel per the execution strategy, since their interfaces are already pinned down in `ARCHITECTURE.md` §5.2 (MCP tool names/signatures) and §5.1 (subagent tool allowlists).
