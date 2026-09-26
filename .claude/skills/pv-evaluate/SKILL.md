---
name: pv-evaluate
description: Runs the Phase 8 evaluation script against accumulated pipeline runs and reports precision/recall/F1 by signal-strength category, the trap-case check, and report-quality checks. Use this after one or more full pipeline runs (via the CLI test script or the Streamlit app) have produced data under runs/ that can be scored against data/ground_truth_signals.json, or whenever the capstone's detection/report-quality behavior needs a fresh accuracy check. Not useful before any run has completed — there is nothing yet to evaluate.
---

# pv-evaluate

Runs the evaluation script that scores the pipeline's actual behavior against the fixed ground-truth answer key, and summarizes the results — this is what proves the detector and report-writer are doing real work, not just producing plausible-looking output.

## When to use this skill

- After one or more end-to-end pipeline runs have completed (CLI test script from Phase 4, or the Streamlit app from Phase 6) and left state under `runs/`.
- Whenever a change to `signal-detector`, `literature-reviewer`, `safety-report-writer`, `mcp_server/server.py`'s statistics, or the guardrail hooks might have affected detection accuracy or report quality, and you want a concrete before/after comparison.
- As part of the periodic rubric checks called for in `CLAUDE.md`/`PLAN.md` (after Phase 4, after Phase 7, full mapping at Phase 9) — evaluation results are one of the concrete artifacts those checks point to.

Do **not** use this skill as a substitute for actually running the pipeline first — it only scores what already happened; it does not itself generate runs.

## What this skill does

1. Confirms `eval/evaluate.py` exists at the expected path. If it does not exist yet (Phase 8 hasn't landed), stop and report that clearly rather than fabricating results.
2. Confirms there is at least one run under `runs/` to evaluate (e.g. `runs/*/state.json` exists). If not, report that no runs were found and that the pipeline needs to be run first — do not invent numbers.
3. Runs it with the project's venv interpreter from the repo root:

   ```bash
   /home/labuser/venv/bin/python3 eval/evaluate.py
   ```

4. Confirms `eval/eval_results.md` was just (re)written, then reads it.

## What to check and report afterward

Read `eval/eval_results.md` and summarize, in plain terms, all of the following (per `ARCHITECTURE.md` §6.1 and `PLAN.md` Phase 8's spec):

- **Precision / recall / F1, broken out by signal-strength category** (`strong` / `borderline` / `noise`), not just an aggregate number. Report each category's numbers explicitly.
- **Trap-case check**: did the pipeline correctly flag the trap pair as statistically significant *and* correctly report it as label-known / not a novel signal? Both halves matter — a trap case that's statistically detected but reported as a novel signal is a guardrail failure (overclaiming novelty), and one that's missed statistically entirely is a detection failure.
- **Report-quality checks**: whether generated reports carry the `SYNTHETIC DEMO — NOT REAL DATA` banner, cite evidence rather than asserting unsupported claims, avoid unhedged causal language ("causes", "proven to cause", etc.), and include an explicit human-approval/review-status field.
- **Cross-run memory check** (if present in the results): whether a pair with prior review history was correctly reported as "previously reviewed and rejected/approved" on a later run rather than presented as brand-new — this is what actually exercises `memory/signal_history.sqlite`, not just per-run state.

## What a good result looks like vs. what should raise concern

- **Good**: strong pairs detected with high precision/recall; the borderline pair's detection is *not* trivially 100% either way — it should show real uncertainty (occasionally missed, or detected with lower confidence language), since a borderline pair that's always cleanly caught (or always cleanly missed) suggests the threshold/generator were tuned to make evaluation trivial rather than to exercise real judgment (see `PLAN.md` Critical Review #4). Noise pairs should show low false-positive rates. The trap case passes both halves of its check. Report-quality checks pass on (nearly) every report.
- **Concerning — flag these explicitly if seen**:
  - The borderline category reads as a flat 100% or flat 0% across runs with no nuance — likely means it isn't actually borderline, or the detector/report-writer isn't engaging real judgment there.
  - The trap case is statistically detected but its report does *not* flag it as label-known/non-novel (a guardrail failure worth escalating immediately — this is the specific overclaiming-novelty failure mode the architecture calls out as a hard guardrail).
  - The trap case isn't statistically detected at all (a detection failure, separate from the guardrail check).
  - Any report missing the synthetic-data banner, missing a human-approval/review-status field, or containing unhedged causal language — these are guardrail regressions, not just accuracy noise.
  - Precision/recall computed from zero or a suspiciously small number of runs — note this so the numbers aren't over-interpreted as statistically meaningful.

Report the numbers plainly, call out anything in the "concerning" list above if present, and don't smooth over a bad result to make the summary sound cleaner than the data supports.
