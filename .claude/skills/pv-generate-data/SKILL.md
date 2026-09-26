---
name: pv-generate-data
description: Regenerates the synthetic AE-report dataset, ground-truth signal answer key, and literature corpus for the pharmacovigilance capstone demo. Use this when synthetic data needs to be (re)generated for a fresh demo run — e.g. after changing generation parameters in data/generate_synthetic_data.py, before a clean end-to-end demo, or when data/db.sqlite is missing/stale/out of sync with data/ground_truth_signals.json. Do not use for anything involving real patient or FAERS data — this project only ever generates synthetic data.
---

# pv-generate-data

Runs the Phase 1 synthetic-data generator and reports a summary of what it produced, so the rest of the pipeline (MCP server, agents, eval) has a fresh, internally-consistent dataset to work against.

## When to use this skill

- The demo needs a clean/fresh dataset (e.g. before a live run-through or after `data/db.sqlite` was deleted).
- `data/generate_synthetic_data.py` itself was edited (new drugs/events, different injected-signal strengths, a different trap case) and its output needs regenerating to match.
- Sanity-checking that the current `data/` artifacts still match what the generator would produce (re-run it — the generator is seeded, so output is deterministic and should be stable unless the script changed).

Do **not** use this skill to fabricate or hand-edit "real" adverse-event data — this project is synthetic-only per `CLAUDE.md` rule #1, and the generator is the single source of truth for how synthetic data is produced.

## What this skill does

1. Confirms `data/generate_synthetic_data.py` exists at the expected path. If it does not exist yet (e.g. a sibling build phase hasn't landed it), stop and report that clearly instead of guessing at an alternative script.
2. Runs it with the project's venv interpreter from the repo root:

   ```bash
   /home/labuser/venv/bin/python3 data/generate_synthetic_data.py
   ```

   (Per `CLAUDE.md`, always use this venv interpreter, not a bare `python`/`python3` that might resolve to a different environment.)

3. Confirms the three expected output artifacts now exist and were just (re)written, per `ARCHITECTURE.md` §6.1:
   - `data/db.sqlite` — `drugs` and `ae_reports` tables.
   - `data/ground_truth_signals.json` — `{"signals": [{drug_name, event_name, strength, is_trap_case}, ...]}`.
   - `data/literature_corpus.json` — `{"publications": [{id, title, drug_name, event_name, stance, text}, ...]}`.

## What to check and report afterward

After running the generator, read its stdout summary and `data/ground_truth_signals.json` and report back:

- **Row counts**: total `ae_reports` row count and drug/event catalog size, from the generator's own summary output (it prints report/drug/event counts). If you need to confirm counts directly against the database, query with the venv's `sqlite3` module rather than assuming the printed numbers — e.g.:

  ```bash
  /home/labuser/venv/bin/python3 -c "
  import sqlite3
  con = sqlite3.connect('data/db.sqlite')
  print('drugs:', con.execute('SELECT COUNT(*) FROM drugs').fetchone()[0])
  print('ae_reports:', con.execute('SELECT COUNT(*) FROM ae_reports').fetchone()[0])
  "
  ```

- **Injected signal pairs and strength categories**: read `data/ground_truth_signals.json` and list every entry's `drug_name`, `event_name`, `strength` (`strong` / `borderline` / `noise`), and whether `is_trap_case` is true. Confirm there is:
  - At least 2 `strong` pairs.
  - At least 1 `borderline` pair (genuinely near the PRR detection threshold in `config.PRR_THRESHOLD` — flag it if the generator's printed PRR for that pair looks either trivially high or basically 1.0, since a borderline pair that isn't actually borderline undermines the evaluation's ability to show real detector work per `PLAN.md` Critical Review #4).
  - Exactly 1 `is_trap_case: true` pair — a statistically real signal that is *already listed* on that drug's synthetic label (check it against that drug's `label_events` in `data/db.sqlite`'s `drugs` table if you want to double-check the label-known claim directly).
  - Some `noise` pairs present, for realistic non-signal background.
- **Engineered-pair PRR sanity check**: the generator prints each engineered pair's `case_count` and `PRR` computed straight from the final data matrix. Report those numbers plainly (drug/event/strength/PRR/case_count) so it's easy to see strong pairs are clearly elevated, the borderline pair is genuinely close to `config.PRR_THRESHOLD` (not clearly above or basically at 1.0), and the trap case is statistically real (PRR comfortably above threshold) despite being label-known.
- **Literature corpus spot check**: report how many publications were generated and roughly how many have `stance: "corroborates"` vs `"contradicts"` vs `"irrelevant"`, so it's clear `literature-reviewer` has real signal (not just noise) to find.

If any expected output file is missing after the run, or the generator errors out, report the failure plainly (including the error output) rather than reporting a false success.
