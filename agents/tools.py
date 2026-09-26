"""LangChain tool wrappers around mcp_client.client's MCP tool functions.

Node code must build each node's tool list by filtering this registry down
to exactly the AgentSpec's allowlisted tool names (see agents/loader.py) --
never bind a tool a spec doesn't list, so least-privilege (CLAUDE.md rule #7)
is enforced at the LangChain tool-binding layer, not just by prompt
instruction.

`record_signal_decision` is intentionally NOT registered here at all -- it is
never bound to any LLM node's tool list. It is called directly (not via LLM
tool-calling) by agents/graph.py's `finalize` node only, after a human
decision has been recorded (CLAUDE.md rule #4). Structurally omitting it from
this registry means no subagent can ever be handed it, even by mistake.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.tools import tool

from mcp_client import client as mcp


@tool
async def scan_signals(prr_threshold: float = 2.0, min_cases: int = 3) -> list[dict]:
    """Server-side disproportionality scan: returns candidate drug-event pairs
    (drug_name, event_name, case_count, prr, background_rate) meeting both
    thresholds, sorted by prr descending. Statistics are computed
    deterministically server-side -- never recompute or alter them."""
    return await mcp.scan_signals(prr_threshold, min_cases)


@tool
async def calculate_prr(drug_name: str, event_name: str) -> dict:
    """On-demand PRR lookup for a single drug-event pair (same formula as
    scan_signals). Returns {drug_name, event_name, case_count, prr, a, b, c, d}."""
    return await mcp.calculate_prr(drug_name, event_name)


@tool
async def get_drug_label(drug_name: str) -> dict:
    """Return the synthetic label events already documented for a drug:
    {drug_name, label_events: list[str]}."""
    return await mcp.get_drug_label(drug_name)


@tool
async def search_literature(query: str) -> list[dict]:
    """Case-insensitive keyword search over the synthetic literature corpus.
    Returns a list of {id, title, drug_name, event_name, stance, text}."""
    return await mcp.search_literature(query)


@tool
async def get_signal_history(drug_name: str, event_name: str) -> dict:
    """Read-only long-term memory lookup: cross-run review history for a
    drug-event pair. Returns {drug_name, event_name, first_seen_run_id,
    history: list[{run_id, decision, reviewer_note, prr_at_decision,
    case_count_at_decision, timestamp}]}. Never errors on no history."""
    return await mcp.get_signal_history(drug_name, event_name)


ALL_TOOLS = {
    t.name: t
    for t in (scan_signals, calculate_prr, get_drug_label, search_literature, get_signal_history)
}


def tools_for(names: list[str]) -> list:
    """Filter ALL_TOOLS down to exactly the given allowlisted names, in order.

    Raises if a spec lists a name this registry doesn't recognize (e.g. a
    typo, or an attempt to allowlist `record_signal_decision`) -- fail loud
    rather than silently under- or over-granting tool access.
    """
    missing = [n for n in names if n not in ALL_TOOLS]
    if missing:
        raise ValueError(f"Unknown tool name(s) in agent spec allowlist: {missing}")
    return [ALL_TOOLS[n] for n in names]
