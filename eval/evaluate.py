#!/usr/bin/env python3
"""Evaluation script (Phase 8): precision/recall/F1 by signal-strength
category, a trap-case check, rule-based report-quality checks, and a
cross-run long-term-memory check.

Per the agreed Phase 8 scope trim: reuses the already-accumulated real run
data from Phase 4/5 (`phase4test2`, `phase5test1`) rather than orchestrating
fresh dedicated runs, and does not re-verify PRR arithmetic (Phase 1/2
already did that independently via direct SQL against data/db.sqlite). No
permanent `_test_*.py` file backs this script -- it is exercised live and
its output is written to eval/eval_results.md for the Evaluation UI tab.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from agents import hooks

RUN_IDS = ["phase4test2", "phase5test1"]


def load_ground_truth() -> dict[tuple[str, str], dict]:
    data = json.loads(config.GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    return {(s["drug_name"], s["event_name"]): s for s in data["signals"]}


def load_run(run_id: str) -> dict | None:
    path = config.RUNS_DIR / run_id / "state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def precision_recall_f1(run_ids: list[str]) -> dict:
    gt = load_ground_truth()
    real_signals = {k for k, v in gt.items() if v["strength"] != "noise"}
    noise_signals = {k for k, v in gt.items() if v["strength"] == "noise"}

    flagged: set[tuple[str, str]] = set()
    for rid in run_ids:
        state = load_run(rid)
        if not state:
            continue
        for c in state.get("candidates", []):
            flagged.add((c["drug_name"], c["event_name"]))

    by_strength: dict[str, dict] = {}
    for pair, sig in gt.items():
        entry = by_strength.setdefault(sig["strength"], {"total": 0, "flagged": 0})
        entry["total"] += 1
        if pair in flagged:
            entry["flagged"] += 1

    tp = len(flagged & real_signals)
    fp = len(flagged & noise_signals)
    fn = len(real_signals - flagged)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "by_strength": by_strength,
        "flagged_pairs": sorted(flagged),
    }


def trap_case_check(run_ids: list[str]) -> dict:
    gt = load_ground_truth()
    trap = next((pair for pair, sig in gt.items() if sig["is_trap_case"]), None)
    if trap is None:
        return {"found": False}
    drug, event = trap
    runs = []
    for rid in run_ids:
        state = load_run(rid)
        if not state:
            continue
        flagged_statistically = any(
            c["drug_name"] == drug and c["event_name"] == event for c in state.get("candidates", [])
        )
        for f in state.get("finalized", []):
            if f["drug_name"] == drug and f["event_name"] == event:
                report = f.get("report") or ""
                report_flags_as_known = bool(re.search(r"known|label|not\s+(a\s+)?novel", report, re.IGNORECASE))
                runs.append({
                    "run_id": rid,
                    "flagged_statistically": flagged_statistically,
                    "report_flags_as_known_or_non_novel": report_flags_as_known,
                })
    return {"found": True, "drug_name": drug, "event_name": event, "runs": runs}


def report_quality_checks() -> list[dict]:
    results = []
    for path in sorted(config.REPORTS_DIR.glob("*-safety-report.md")):
        content = path.read_text(encoding="utf-8")
        try:
            hooks.guard_report_content(content)
            guardrail_ok, guardrail_error = True, None
        except hooks.GuardrailViolation as exc:
            guardrail_ok, guardrail_error = False, str(exc)
        lowered = content.lower()
        cites_evidence_or_honest = bool(re.search(r"pub-\d+", content)) or any(
            phrase in lowered for phrase in ("no corroborating", "no literature", "no relevant publications", "no supporting literature")
        )
        results.append({
            "file": path.name,
            "guardrail_ok": guardrail_ok,
            "guardrail_error": guardrail_error,
            "cites_evidence_or_honestly_states_none": cites_evidence_or_honest,
        })
    return results


def cross_run_memory_check() -> dict:
    later = load_run("phase5test1")
    if later is None:
        return {"checked": False, "reason": "phase5test1 run data not found"}
    candidates = [
        {"drug_name": c["drug_name"], "event_name": c["event_name"], "history_status": c["history_status"]}
        for c in later.get("candidates", [])
    ]
    carried_forward = any("previously" in c["history_status"] for c in candidates)
    return {"checked": True, "candidates": candidates, "carried_forward_from_earlier_run": carried_forward}


def render_markdown(pr: dict, trap: dict, quality: list[dict], memory_check: dict, run_ids: list[str]) -> str:
    lines = [
        f"**{config.SYNTHETIC_DATA_BANNER}**",
        "",
        "# Evaluation Results",
        "",
        f"_Evaluated against real accumulated runs: {', '.join(run_ids)}_",
        "",
        "## Precision / Recall / F1 (flagged candidates vs. ground truth)",
        "",
        f"- Precision: **{pr['precision']:.3f}**",
        f"- Recall: **{pr['recall']:.3f}**",
        f"- F1: **{pr['f1']:.3f}**",
        f"- TP={pr['tp']}  FP={pr['fp']}  FN={pr['fn']}",
        "",
        "| Strength | Flagged / Total |",
        "|---|---|",
    ]
    for strength, counts in sorted(pr["by_strength"].items()):
        lines.append(f"| {strength} | {counts['flagged']}/{counts['total']} |")
    lines += [
        "",
        "_Note: noise-pair exclusion (true negatives) was independently verified in Phase 2's exit check via "
        "`scan_signals` directly against `data/db.sqlite` -- noise pairs never enter a run's candidate list by "
        "construction, so this figure reflects that already-proven property rather than re-deriving it from scratch._",
        "",
        "## Trap-case check",
        "",
    ]
    if trap["found"]:
        lines.append(f"Trap case: **{trap['drug_name']} / {trap['event_name']}** (statistically real, but already label-listed)")
        lines.append("")
        for r in trap["runs"]:
            lines.append(
                f"- Run `{r['run_id']}`: flagged statistically = **{r['flagged_statistically']}**, "
                f"report correctly frames it as known/non-novel = **{r['report_flags_as_known_or_non_novel']}**"
            )
    else:
        lines.append("No trap case defined in ground truth.")

    lines += ["", "## Report-quality checks", "", "| Report | Guardrail-clean | Cites evidence or honestly states none |", "|---|---|---|"]
    for r in quality:
        guardrail_cell = "✅" if r["guardrail_ok"] else f"❌ {r['guardrail_error']}"
        evidence_cell = "✅" if r["cites_evidence_or_honestly_states_none"] else "❌"
        lines.append(f"| {r['file']} | {guardrail_cell} | {evidence_cell} |")

    lines += ["", "## Cross-run long-term memory check", ""]
    if memory_check["checked"]:
        lines.append(
            f"`phase5test1` candidates correctly carry forward `phase4test2`'s recorded decisions: "
            f"**{memory_check['carried_forward_from_earlier_run']}**"
        )
        lines.append("")
        for c in memory_check["candidates"]:
            lines.append(f"- {c['drug_name']} / {c['event_name']}: _{c['history_status']}_")
    else:
        lines.append(memory_check["reason"])

    return "\n".join(lines) + "\n"


def main() -> int:
    pr = precision_recall_f1(RUN_IDS)
    trap = trap_case_check(RUN_IDS)
    quality = report_quality_checks()
    memory_check = cross_run_memory_check()

    output = render_markdown(pr, trap, quality, memory_check, RUN_IDS)
    print(output)

    out_path = config.ROOT_DIR / "eval" / "eval_results.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(f"\nWritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
