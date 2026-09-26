#!/usr/bin/env python3
"""CLI test harness for the pv-signal-detection LangGraph pipeline (Phase 4).

Each invocation is a fresh process: it opens its own AsyncSqliteSaver
connection against config.CHECKPOINT_DB_PATH and its own MCPClient
(spawning a fresh mcp_server/server.py subprocess), builds the graph, and
either starts a new run or resumes a paused one -- then exits. Running
`--decision ...` against the same --run-id in a brand new process invocation
is exactly the "simulated process restart between pause and resume" the
Phase 4 exit check requires -- nothing is held in this process's memory
across invocations, only in runs/checkpoints.sqlite.

Usage:
  # start a fresh run; runs until the first human-approval interrupt (or END
  # immediately, if scan_signals returns no candidates)
  python3 agents/cli_test_run.py --run-id demo1

  # resume that run with a human decision; runs until the next interrupt or
  # full completion
  python3 agents/cli_test_run.py --run-id demo1 --decision approved --note "looks solid"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from agents.graph import build_graph
from agents.state import new_state
from mcp_client.client import MCPClient

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--decision", choices=["approved", "rejected", "sent_back"], default=None)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    config.CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with MCPClient() as mcp, AsyncSqliteSaver.from_conn_string(
        str(config.CHECKPOINT_DB_PATH)
    ) as checkpointer:
        graph = build_graph(checkpointer, mcp)
        thread_config = {"configurable": {"thread_id": args.run_id}}

        if args.decision is None:
            existing = await graph.aget_state(thread_config)
            if existing.values:
                print(
                    f"run {args.run_id!r} already has state; pass --decision to resume it, "
                    "or use a new --run-id to start fresh.",
                    file=sys.stderr,
                )
                return 1
            graph_input = new_state(args.run_id)
        else:
            graph_input = Command(resume={"decision": args.decision, "reviewer_note": args.note})

        result = await graph.ainvoke(graph_input, config=thread_config)

        interrupts = result.get("__interrupt__")
        if interrupts:
            payload = interrupts[0].value
            print("\n=== PAUSED for human approval ===")
            print(json.dumps(payload, indent=2, default=str))
            print(
                f"\nResume with: python3 agents/cli_test_run.py --run-id {args.run_id} "
                '--decision approved|rejected|sent_back --note "..."'
            )
        else:
            print("\n=== RUN COMPLETE ===")
            finalized = result.get("finalized", [])
            print(f"finalized {len(finalized)} candidate(s)")
            for f in finalized:
                print(f"  - {f['drug_name']} / {f['event_name']}: {f['decision']}")

        print("\n--- trace ---")
        for step in result.get("trace", []):
            content = step["content"].replace("\n", " ")[:160]
            print(f"[{step['agent']:>20}] {step['step_type']:<12} {content}")

        # All meaningful work is done and printed above. Force the buffer to
        # disk now: stdout is fully (not line-) buffered when redirected to a
        # file/pipe, and asyncio.run()'s post-return cleanup of any lingering
        # anyio/httpx background tasks (observed to occasionally stall for
        # minutes on this stack, unrelated to pipeline correctness) would
        # otherwise risk the real output never reaching disk if the process
        # is later killed impatiently.
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
