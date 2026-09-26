"""Shared LangGraph state schema and per-run persistence.

Two-tier memory (see ARCHITECTURE.md section 5.3):
  - This module defines the SHORT-TERM/working state: scoped to one run
    (thread_id == run_id), durable for the run's lifetime via the sqlite
    checkpointer (runs/checkpoints.sqlite) plus the plain-JSON snapshot
    this module writes to runs/<run_id>/state.json for easy inspection.
  - LONG-TERM/cross-run memory lives separately in
    memory/signal_history.sqlite, owned by mcp_server/server.py, and is
    not part of this schema.
"""

import json
import sys
from pathlib import Path
from typing import Literal, Optional, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

StepType = Literal["reasoning", "tool_call", "tool_result", "output"]


class StepRecord(TypedDict):
    """One entry in a run's trace -- feeds both the Live Run and Observability UI tabs."""

    agent: str
    step_type: StepType
    content: str
    timestamp: str


class Candidate(TypedDict):
    """One drug-event pair as prioritized by signal-detector."""

    drug_name: str
    event_name: str
    case_count: int
    prr: float
    background_rate: float
    history_status: str


class LiteratureFindings(TypedDict):
    drug_name: str
    event_name: str
    corroborating: list[dict]
    contradicting: list[dict]
    label_known: bool
    summary: str


class PipelineState(TypedDict):
    """The LangGraph State for the pv-signal-detection graph.

    Candidates are processed sequentially (see ARCHITECTURE.md section 3 /
    PLAN.md critical review #3) -- never fanned out in parallel -- so there
    is exactly one "current candidate" in flight at a time, tracked by
    current_candidate_index into candidates.
    """

    run_id: str
    candidates: list[Candidate]
    current_candidate_index: int
    current_literature: Optional[LiteratureFindings]
    current_report: Optional[str]
    pending_decision: Optional[str]
    finalized: list[dict]
    trace: list[StepRecord]


def new_state(run_id: str) -> PipelineState:
    return PipelineState(
        run_id=run_id,
        candidates=[],
        current_candidate_index=0,
        current_literature=None,
        current_report=None,
        pending_decision=None,
        finalized=[],
        trace=[],
    )


def _run_dir(run_id: str) -> Path:
    return config.RUNS_DIR / run_id


def save_state(state: PipelineState) -> Path:
    """Write a plain-JSON snapshot of the given state to runs/<run_id>/state.json.

    This is a human-inspectable side artifact -- the sqlite checkpointer
    (runs/checkpoints.sqlite) is the source of truth LangGraph itself
    resumes from; this file is not read back by the graph.
    """
    run_dir = _run_dir(state["run_id"])
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "state.json"
    path.write_text(json.dumps(state, indent=2, default=str))
    return path


def load_state(run_id: str) -> Optional[PipelineState]:
    path = _run_dir(run_id) / "state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
