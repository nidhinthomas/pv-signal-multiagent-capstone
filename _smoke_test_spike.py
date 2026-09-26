"""Throwaway Phase 0 spike -- NOT part of the final codebase.

Confirms three things Phase 4 depends on, before building the real pipeline:
1. The OpenRouter Claude model slug works via langchain_openai.ChatOpenAI.
2. .stream() actually streams incrementally through OpenRouter.
3. A minimal one-node LangGraph graph with interrupt() + SqliteSaver
   pauses and resumes correctly across a simulated process restart.

Delete this file once Phase 4 is built and has its own real tests.
"""

import os
import sys

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from typing_extensions import TypedDict

sys.path.insert(0, os.path.dirname(__file__))
import config


def check_1_and_2_llm_and_streaming():
    print("=== Check 1 & 2: OpenRouter model slug + streaming ===")
    llm = ChatOpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=os.environ[config.OPENROUTER_API_KEY_ENV],
        model=config.OPENROUTER_MODEL_SLUG,
    )
    chunks = []
    for chunk in llm.stream("Reply with exactly the word: PONG"):
        piece = chunk.content
        if piece:
            chunks.append(piece)
            print(f"  [chunk {len(chunks)}] {piece!r}")
    full = "".join(chunks)
    assert len(chunks) >= 1, "No streamed chunks received"
    assert "PONG" in full.upper(), f"Unexpected response: {full!r}"
    print(f"  PASS -- {len(chunks)} chunk(s), full response: {full!r}\n")


class SpikeState(TypedDict):
    value: str


def node_with_interrupt(state: SpikeState) -> SpikeState:
    decision = interrupt({"question": "approve?", "current_value": state["value"]})
    return {"value": f"{state['value']}-{decision}"}


def build_spike_graph(checkpointer):
    graph = StateGraph(SpikeState)
    graph.add_node("pause_here", node_with_interrupt)
    graph.add_edge(START, "pause_here")
    graph.add_edge("pause_here", END)
    return graph.compile(checkpointer=checkpointer)


def check_3_interrupt_and_resume():
    print("=== Check 3: LangGraph interrupt() + SqliteSaver pause/resume ===")
    db_path = os.path.join(os.path.dirname(__file__), "_smoke_test_checkpoints.sqlite")
    if os.path.exists(db_path):
        os.remove(db_path)

    thread_id = "spike-thread-1"
    config_dict = {"configurable": {"thread_id": thread_id}}

    # --- "Process 1": start the run, expect it to pause at the interrupt ---
    with SqliteSaver.from_conn_string(db_path) as checkpointer:
        graph = build_spike_graph(checkpointer)
        result = graph.invoke({"value": "start"}, config=config_dict)
        assert "__interrupt__" in result, f"Expected pause, got: {result}"
        print(f"  Paused as expected: {result['__interrupt__']}")
    # checkpointer connection closed here -- simulates process exit

    # --- "Process 2": fresh connection to the same sqlite file, resume ---
    with SqliteSaver.from_conn_string(db_path) as checkpointer:
        graph = build_spike_graph(checkpointer)
        final = graph.invoke(Command(resume="approved"), config=config_dict)
        assert final["value"] == "start-approved", f"Unexpected final state: {final}"
        print(f"  Resumed correctly after simulated restart: {final}")

    os.remove(db_path)
    print("  PASS -- interrupt/resume survives a fresh checkpointer connection\n")


if __name__ == "__main__":
    check_1_and_2_llm_and_streaming()
    check_3_interrupt_and_resume()
    print("ALL SMOKE TESTS PASSED")
