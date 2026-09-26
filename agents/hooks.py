"""Runtime guardrail + observability layer wrapping agents/graph.py's node
execution and tool calls (ARCHITECTURE.md §5.4; CLAUDE.md rules #1, #4, #5).

Two pre-call guardrails, each enforced independently of the code path it
guards -- so a future bug or edit elsewhere can't silently bypass them:

- `guard_record_signal_decision`: the SECOND of CLAUDE.md rule #4's two
  required enforcement points for `record_signal_decision` (the first is
  Phase 3's tool allowlisting -- the tool is simply absent from
  agents/tools.py's ALL_TOOLS registry, so no subagent's LLM tool-calling
  loop can ever reach it at all). This one checks, at the moment of the
  actual call, that the caller really is `finalize_node` -- via the real
  Python call stack, not a self-reported flag the caller could get wrong
  -- and that a human decision has actually been recorded in state.
- `guard_report_content`: blocks persisting a report that is missing the
  synthetic-data banner or a review-status/approval marker, or that
  contains unhedged causal language (CLAUDE.md rule #1). Negation-aware
  (`_is_hedged`): a bad phrase inside a negated span -- e.g. "does not
  constitute a determination that X causes Y", the exact cautious
  hedging safety-report-writer is required to produce -- is not a
  violation of the guardrail it is written to satisfy; a real live run
  (phase5test1, candidate 2) hit this false positive, which is why the
  check is negation-aware rather than a bare substring match. Mirrors the
  dev-ops-layer checks in .claude/hooks/validate_report.py, but as a real
  runtime gate on what finalize_node actually writes, not a Claude Code
  session hook (those only fire during CC CLI sessions; this pipeline
  runs standalone).

Post-call observability: `log_event` and its typed wrappers
(`log_tool_call`, `log_llm_turn`, `timed_node`) append one JSON line per
node execution / tool call / guardrail block / error to
`config.OBSERVABILITY_LOG_PATH`, per ARCHITECTURE.md §5.4's schema (agent,
tool, args summary, latency, timestamp, model/token usage where
available).

Note on the third §5.4 guardrail ("block filesystem reads outside
data/db.sqlite"): no MCP tool accepts a caller-supplied file path at all
(see ARCHITECTURE.md §6.1's signatures, and mcp_server/server.py directly)
-- every data-file access is hardcoded per-tool to a config.py path
constant, so there is no dynamic path input for a runtime check here to
guard. See ARCHITECTURE.md §9's divergence log entry for the full
reasoning (including why a redundant check against
config.ALLOWED_DATA_PATHS would conflict with mcp_server/_test_tools.py's
env-var fixture overrides).
"""

from __future__ import annotations

import inspect
import json
import time
from contextlib import contextmanager
from typing import Any, Optional

import config


class GuardrailViolation(RuntimeError):
    """Raised when a runtime guardrail blocks an action. Must never be
    caught and silently swallowed by pipeline code -- every guardrail here
    backs a CLAUDE.md business rule, so a violation aborts the run."""


# --- Pre-call guardrails ---------------------------------------------------


def guard_record_signal_decision(state: dict) -> None:
    """Must be called immediately before every `record_signal_decision`
    call, from the call site itself (so `inspect.stack()[1]` is that call
    site's own function, not some indirection layer)."""
    caller = inspect.stack()[1].function
    if caller != "finalize_node":
        raise GuardrailViolation(
            f"record_signal_decision blocked: called from {caller!r}, not "
            "finalize_node (CLAUDE.md rule #4)"
        )
    if state.get("pending_decision") is None:
        raise GuardrailViolation(
            "record_signal_decision blocked: no human decision recorded in "
            "state['pending_decision'] (CLAUDE.md rule #5)"
        )


_APPROVAL_MARKERS = ("review status", "approval")
_BAD_PHRASES = ("causes", "proven to cause", "confirmed to cause")
_NEGATION_WINDOW = 80
_NEGATION_MARKERS = (
    "not ", "n't ", "no evidence", "cannot", "without", "rather than",
    "does not constitute", "not a causal", "not causal", "not a confirmed",
)


def _is_hedged(lowered: str, match_start: int) -> bool:
    """A bad-phrase hit inside a negated span (e.g. "does not constitute a
    determination that X causes Y") is exactly the cautious hedging
    safety-report-writer is required to produce, not a violation of it --
    checking a preceding window for negation markers avoids flagging the
    guardrail's own intended behavior."""
    window = lowered[max(0, match_start - _NEGATION_WINDOW):match_start]
    return any(marker in window for marker in _NEGATION_MARKERS)


def guard_report_content(content: str) -> None:
    """Must be called on the exact bytes about to be persisted, before the
    write -- not on the model's pre-banner draft."""
    failures = []
    if config.SYNTHETIC_DATA_BANNER not in content:
        failures.append(f"missing required banner {config.SYNTHETIC_DATA_BANNER!r}")
    lowered = content.lower()
    if not any(marker in lowered for marker in _APPROVAL_MARKERS):
        failures.append("missing a review-status/approval marker")
    bad_hits = []
    for phrase in _BAD_PHRASES:
        start = 0
        while True:
            idx = lowered.find(phrase, start)
            if idx == -1:
                break
            if not _is_hedged(lowered, idx):
                bad_hits.append(phrase)
                break
            start = idx + len(phrase)
    if bad_hits:
        failures.append("contains unhedged causal language: " + ", ".join(bad_hits))
    if failures:
        raise GuardrailViolation("report guardrail blocked write: " + "; ".join(failures))


# --- Post-call observability ----------------------------------------------


def log_event(event_type: str, **fields: Any) -> None:
    """Append one JSON line to logs/observability.jsonl. Never raises --
    an observability-logging failure must not take down the pipeline."""
    entry = {
        "event_type": event_type,
        "timestamp": time.time(),
        **fields,
    }
    try:
        config.OBSERVABILITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with config.OBSERVABILITY_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def log_guardrail_block(run_id: str, agent: str, violation: "GuardrailViolation") -> None:
    log_event("guardrail_block", run_id=run_id, agent=agent, error=str(violation))


def log_tool_call(
    run_id: str,
    agent: str,
    tool_name: str,
    args: dict,
    latency_ms: float,
    success: bool,
    result_summary: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    log_event(
        "tool_call",
        run_id=run_id,
        agent=agent,
        tool=tool_name,
        args=args,
        latency_ms=round(latency_ms, 1),
        success=success,
        result_summary=result_summary,
        error=error,
    )


def log_llm_turn(run_id: str, agent: str, latency_ms: float, usage: Optional[dict]) -> None:
    log_event(
        "llm_turn",
        run_id=run_id,
        agent=agent,
        model=config.OPENROUTER_MODEL_SLUG,
        latency_ms=round(latency_ms, 1),
        usage=usage,
    )


@contextmanager
def timed_node(run_id: str, agent: str):
    """Wraps one node's execution: logs node_start, then node_end (success)
    or node_error (failure) with latency, to logs/observability.jsonl.

    Not used around human_approval_node -- LangGraph's interrupt() raises a
    control-flow exception to pause the graph, which this would otherwise
    misreport as a node_error."""
    start = time.monotonic()
    log_event("node_start", run_id=run_id, agent=agent)
    try:
        yield
    except Exception as exc:
        log_event(
            "node_error",
            run_id=run_id,
            agent=agent,
            latency_ms=round((time.monotonic() - start) * 1000, 1),
            error=str(exc),
        )
        raise
    else:
        log_event(
            "node_end",
            run_id=run_id,
            agent=agent,
            latency_ms=round((time.monotonic() - start) * 1000, 1),
        )


def summarize_result(result: Any, limit: int = 300) -> str:
    text = json.dumps(result, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."
