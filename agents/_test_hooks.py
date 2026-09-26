"""Self-contained unit test for agents/hooks.py's guardrails + observability
logging (Phase 5 exit check, PLAN.md). Kept as a real regression test, not
thrown away, per the pattern established by mcp_server/_test_tools.py.

Deliberately triggers both required guardrail violations (a report missing
its disclaimer, and a record_signal_decision call from a non-finalize_node
context) and confirms both are blocked; also confirms the happy path
passes and that observability logging writes well-formed JSON lines.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from agents import hooks

checks = 0
failures = []


def check(label: str, condition: bool) -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(label)
    print(f"{'PASS' if condition else 'FAIL'}: {label}")


GOOD_REPORT = (
    f"**{config.SYNTHETIC_DATA_BANNER}**\n\n"
    "This is consistent with a possible association warranting further review.\n\n"
    "## Review Status\n\n- **Decision:** approved\n"
)

# --- guard_report_content: happy path -------------------------------------

try:
    hooks.guard_report_content(GOOD_REPORT)
    check("guard_report_content: well-formed report passes", True)
except hooks.GuardrailViolation:
    check("guard_report_content: well-formed report passes", False)

# --- guard_report_content: deliberately strip the disclaimer/banner ------

stripped = GOOD_REPORT.replace(config.SYNTHETIC_DATA_BANNER, "")
try:
    hooks.guard_report_content(stripped)
    check("guard_report_content: missing banner is BLOCKED", False)
except hooks.GuardrailViolation as exc:
    check("guard_report_content: missing banner is BLOCKED", "banner" in str(exc))

# --- guard_report_content: missing review-status/approval marker ---------

no_marker = f"**{config.SYNTHETIC_DATA_BANNER}**\n\nJust some findings text.\n"
try:
    hooks.guard_report_content(no_marker)
    check("guard_report_content: missing approval marker is BLOCKED", False)
except hooks.GuardrailViolation as exc:
    check("guard_report_content: missing approval marker is BLOCKED", "marker" in str(exc))

# --- guard_report_content: unhedged causal language -----------------------

causal = GOOD_REPORT + "\nThis drug causes the event.\n"
try:
    hooks.guard_report_content(causal)
    check("guard_report_content: unhedged causal language is BLOCKED", False)
except hooks.GuardrailViolation as exc:
    check("guard_report_content: unhedged causal language is BLOCKED", "causal" in str(exc))

# --- guard_report_content: negated/hedged "causes" is NOT a false positive,
# per the real phase5test1 finalize crash this regression case reproduces --
# safety-report-writer legitimately wrote "does not constitute a
# determination that Vastocor causes ... tendon rupture", which a naive
# substring match on "causes" wrongly blocked ----------------------------

hedged_causal = GOOD_REPORT + (
    "\nThis characterization does not constitute a determination that "
    "Vastocor causes or is responsible for tendon rupture.\n"
)
try:
    hooks.guard_report_content(hedged_causal)
    check("guard_report_content: negated 'causes' inside a hedge is NOT blocked", True)
except hooks.GuardrailViolation:
    check("guard_report_content: negated 'causes' inside a hedge is NOT blocked", False)

# --- guard_record_signal_decision: happy path (called from a function ----
# literally named finalize_node, simulating the real call site) -----------


def finalize_node(state):
    hooks.guard_record_signal_decision(state)


try:
    finalize_node({"pending_decision": json.dumps({"decision": "approved"})})
    check("guard_record_signal_decision: called from finalize_node with a decision passes", True)
except hooks.GuardrailViolation:
    check("guard_record_signal_decision: called from finalize_node with a decision passes", False)

# --- guard_record_signal_decision: wrong caller is BLOCKED ----------------


def some_other_node(state):
    hooks.guard_record_signal_decision(state)


try:
    some_other_node({"pending_decision": json.dumps({"decision": "approved"})})
    check("guard_record_signal_decision: call from a non-finalize_node context is BLOCKED", False)
except hooks.GuardrailViolation as exc:
    check(
        "guard_record_signal_decision: call from a non-finalize_node context is BLOCKED",
        "some_other_node" in str(exc),
    )

# --- guard_record_signal_decision: no recorded decision is BLOCKED -------


try:
    finalize_node({"pending_decision": None})
    check("guard_record_signal_decision: no recorded human decision is BLOCKED", False)
except hooks.GuardrailViolation as exc:
    check(
        "guard_record_signal_decision: no recorded human decision is BLOCKED",
        "no human decision" in str(exc),
    )

# --- observability logging -------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    original_path = config.OBSERVABILITY_LOG_PATH
    config.OBSERVABILITY_LOG_PATH = Path(tmp) / "observability.jsonl"
    try:
        hooks.log_event("tool_call", run_id="t1", agent="signal-detector", tool="scan_signals")
        hooks.log_guardrail_block("t1", "finalize", hooks.GuardrailViolation("boom"))
        with hooks.timed_node("t1", "signal-detector"):
            pass
        lines = config.OBSERVABILITY_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        parsed = [json.loads(line) for line in lines]
        check("log_event: writes one JSON line per call", len(parsed) == 4)
        check(
            "timed_node: emits node_start then node_end",
            [p["event_type"] for p in parsed[-2:]] == ["node_start", "node_end"],
        )
        check("log_guardrail_block: event_type is guardrail_block", parsed[1]["event_type"] == "guardrail_block")
    finally:
        config.OBSERVABILITY_LOG_PATH = original_path

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
