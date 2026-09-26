"""LangGraph pipeline: signal_detector -> (literature_reviewer ->
safety_report_writer -> human_approval[interrupt] -> finalize) repeated once
per candidate, strictly sequentially -- never fanned out in parallel
(CLAUDE.md rule #6 / ARCHITECTURE.md / PLAN.md Critical Review #3).

Design note on structured vs. narrative output: each LLM-facing node still
produces the delimited ### Reasoning / ### Conclusion prose its `.claude/agents/*.md`
spec requires (captured into `state.trace` for observability, and, for
safety-report-writer, used as the report text itself). But nothing that
drives pipeline *control flow or state* (candidate stats, history status,
label-known status, cited publications) is parsed out of that prose --
every such field is derived directly from the real tool-call results captured
during the node's own tool-calling turn, per CLAUDE.md's "never fabricate or
alter statistics" / "never fabricate history" constraints applied at the
orchestrator level too, not just trusted from the model's restatement.

record_signal_decision is called exactly once, directly (not via LLM
tool-calling -- it is not in agents/tools.py's registry at all), from
`finalize_node`, only after a human decision has been captured by the
`human_approval` interrupt node (CLAUDE.md rule #4/#5).
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from agents import tools as pv_tools
from agents.loader import AgentSpec, load_all_agents
from agents.state import Candidate, LiteratureFindings, PipelineState, StepRecord, save_state
from mcp_client.client import MCPClient

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

SPECS = load_all_agents()
MAX_TOOL_ITERATIONS = 10


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _make_llm(tool_objs: list):
    llm = ChatOpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=os.environ[config.OPENROUTER_API_KEY_ENV],
        model=config.OPENROUTER_MODEL_SLUG,
    )
    return llm.bind_tools(tool_objs) if tool_objs else llm


def _split_reasoning_conclusion(text: str) -> tuple[str, str]:
    """Split a node's raw output on its required ### Reasoning / ### Conclusion
    delimiters. Falls back to (\"\", text) if a node ever fails to follow the
    format, rather than crashing the pipeline over a formatting slip."""
    if "### Reasoning" in text and "### Conclusion" in text:
        _, rest = text.split("### Reasoning", 1)
        reasoning, conclusion = rest.split("### Conclusion", 1)
        return reasoning.strip(), conclusion.strip()
    return "", text.strip()


async def _run_agent_turn(
    agent_name: str,
    spec: AgentSpec,
    user_content: str,
    trace: list[StepRecord],
) -> tuple[str, list[dict]]:
    """Runs one subagent's full tool-calling turn through to a final answer.

    Appends a trace StepRecord for every tool call, every tool result, the
    final reasoning text, and the final full output (mutates `trace` in
    place). Returns (final_raw_text, tool_call_log), where tool_call_log is
    [{tool_name, args, result}, ...] in call order -- the caller derives all
    machine-consumed fields from this log, never from parsing final_raw_text.
    """
    tool_objs = pv_tools.tools_for(spec.tools) if spec.tools else []
    llm = _make_llm(tool_objs)
    messages: list = [SystemMessage(content=spec.system_prompt), HumanMessage(content=user_content)]
    tool_call_log: list[dict] = []

    for _ in range(MAX_TOOL_ITERATIONS):
        full: Optional[AIMessage] = None
        async for chunk in llm.astream(messages):
            full = chunk if full is None else full + chunk
        if full is None:
            raise RuntimeError(f"{agent_name}: LLM produced no output")

        if full.tool_calls:
            messages.append(full)
            for tc in full.tool_calls:
                trace.append(
                    StepRecord(
                        agent=agent_name,
                        step_type="tool_call",
                        content=json.dumps({"tool": tc["name"], "args": tc["args"]}, default=str),
                        timestamp=_now(),
                    )
                )
                tool = pv_tools.ALL_TOOLS[tc["name"]]
                result = await tool.ainvoke(tc["args"])
                tool_call_log.append({"tool_name": tc["name"], "args": tc["args"], "result": result})
                trace.append(
                    StepRecord(
                        agent=agent_name,
                        step_type="tool_result",
                        content=json.dumps({"tool": tc["name"], "result": result}, default=str),
                        timestamp=_now(),
                    )
                )
                messages.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=tc["id"]))
            continue

        text = full.content or ""
        reasoning, _conclusion = _split_reasoning_conclusion(text)
        if reasoning:
            trace.append(StepRecord(agent=agent_name, step_type="reasoning", content=reasoning, timestamp=_now()))
        trace.append(StepRecord(agent=agent_name, step_type="output", content=text, timestamp=_now()))
        return text, tool_call_log

    raise RuntimeError(f"{agent_name}: exceeded {MAX_TOOL_ITERATIONS} tool-calling iterations without a final answer")


def _history_status(history_result: Optional[dict]) -> str:
    if not history_result or not history_result.get("history"):
        return "new signal, no prior history"
    latest = history_result["history"][-1]
    decision = latest.get("decision")
    if decision == "approved":
        return "previously reviewed and approved -- still monitored"
    if decision == "rejected":
        return "previously reviewed and rejected -- resurfacing"
    if decision == "sent_back":
        return "previously sent back for more evidence"
    return "previously reviewed"


async def signal_detector_node(state: PipelineState, mcp: MCPClient) -> dict:
    spec = SPECS["signal-detector"]
    user_content = (
        f"Run the signal-detection pass for run_id={state['run_id']!r}. Call scan_signals with the "
        "default thresholds, then call get_signal_history for every candidate pair it returns, then "
        "produce your prioritized list per your instructions."
    )
    text, tool_log = await _run_agent_turn("signal-detector", spec, user_content, state["trace"])

    scan_result = next((e["result"] for e in tool_log if e["tool_name"] == "scan_signals"), [])
    history_by_pair = {
        (e["args"]["drug_name"], e["args"]["event_name"]): e["result"]
        for e in tool_log
        if e["tool_name"] == "get_signal_history"
    }

    _, conclusion = _split_reasoning_conclusion(text)

    def first_mention(drug_name: str) -> int:
        idx = conclusion.find(drug_name)
        return idx if idx >= 0 else len(conclusion) + 1

    ordered = sorted(scan_result, key=lambda r: first_mention(r["drug_name"]))

    candidates: list[Candidate] = [
        Candidate(
            drug_name=r["drug_name"],
            event_name=r["event_name"],
            case_count=r["case_count"],
            prr=r["prr"],
            background_rate=r["background_rate"],
            history_status=_history_status(history_by_pair.get((r["drug_name"], r["event_name"]))),
        )
        for r in ordered
    ]
    return {"candidates": candidates, "current_candidate_index": 0}


async def literature_reviewer_node(state: PipelineState, mcp: MCPClient) -> dict:
    spec = SPECS["literature-reviewer"]
    candidate = state["candidates"][state["current_candidate_index"]]
    user_content = (
        f"Review literature and label status for drug_name={candidate['drug_name']!r}, "
        f"event_name={candidate['event_name']!r}. Statistical context already established by "
        f"signal-detector: case_count={candidate['case_count']}, prr={candidate['prr']:.2f}, "
        f"background_rate={candidate['background_rate']:.4f}, history_status={candidate['history_status']!r}."
    )
    text, tool_log = await _run_agent_turn("literature-reviewer", spec, user_content, state["trace"])

    label_result = next((e["result"] for e in reversed(tool_log) if e["tool_name"] == "get_drug_label"), None)
    label_known = bool(label_result and candidate["event_name"] in (label_result.get("label_events") or []))

    corroborating: list[dict] = []
    contradicting: list[dict] = []
    seen_ids: set = set()
    for e in tool_log:
        if e["tool_name"] != "search_literature":
            continue
        for pub in e["result"] or []:
            pub_id = pub.get("id")
            if pub_id in seen_ids:
                continue
            seen_ids.add(pub_id)
            if pub.get("stance") == "corroborates":
                corroborating.append(pub)
            elif pub.get("stance") == "contradicts":
                contradicting.append(pub)

    _, conclusion = _split_reasoning_conclusion(text)
    literature = LiteratureFindings(
        drug_name=candidate["drug_name"],
        event_name=candidate["event_name"],
        corroborating=corroborating,
        contradicting=contradicting,
        label_known=label_known,
        summary=conclusion,
    )
    return {"current_literature": literature}


async def safety_report_writer_node(state: PipelineState, mcp: MCPClient) -> dict:
    spec = SPECS["safety-report-writer"]
    candidate = state["candidates"][state["current_candidate_index"]]
    lit = state["current_literature"]
    assert lit is not None, "safety_report_writer_node requires current_literature to be set"

    user_content = (
        "Statistical finding (from signal-detector, server-computed -- do not alter):\n"
        f"  drug_name={candidate['drug_name']!r}, event_name={candidate['event_name']!r}\n"
        f"  case_count={candidate['case_count']}, prr={candidate['prr']:.2f}, "
        f"background_rate={candidate['background_rate']:.4f}\n"
        f"  history_status={candidate['history_status']!r}\n"
        f"  (this system's PRR detection threshold is {config.PRR_THRESHOLD})\n\n"
        "Literature finding (from literature-reviewer):\n"
        f"  label_known={lit['label_known']!r}\n"
        f"  corroborating={json.dumps(lit['corroborating'], default=str)}\n"
        f"  contradicting={json.dumps(lit['contradicting'], default=str)}\n"
        f"  literature-reviewer's notes: {lit['summary']}\n\n"
        f"Synthetic-data banner text to include verbatim: {config.SYNTHETIC_DATA_BANNER!r}\n\n"
        "Draft the report now, per your instructions."
    )
    text, _tool_log = await _run_agent_turn("safety-report-writer", spec, user_content, state["trace"])
    _, conclusion = _split_reasoning_conclusion(text)
    return {"current_report": conclusion}


def human_approval_node(state: PipelineState) -> dict:
    candidate = state["candidates"][state["current_candidate_index"]]
    decision_payload = interrupt(
        {
            "run_id": state["run_id"],
            "drug_name": candidate["drug_name"],
            "event_name": candidate["event_name"],
            "case_count": candidate["case_count"],
            "prr": candidate["prr"],
            "history_status": candidate["history_status"],
            "report": state["current_report"],
            "instructions": (
                "Resume with Command(resume={'decision': 'approved'|'rejected'|'sent_back', "
                "'reviewer_note': str})"
            ),
        }
    )
    return {"pending_decision": json.dumps(decision_payload)}


def _write_report_file(state: PipelineState, candidate: Candidate, decision: str, reviewer_note: str) -> Path:
    """Persists the reviewer-ready report to reports/<run_id>-<n>-safety-report.md,
    appending a Review Status section (decision + reviewer note) now that a
    human decision actually exists -- safety-report-writer itself has no
    tools and drafts before the decision is made, so it cannot include this
    section itself. Ensures the synthetic-data banner is present even if the
    model's draft omitted it, since CLAUDE.md rule #1 requires it on every
    generated report as a literal field, not just a UI overlay."""
    draft = state["current_report"] or ""
    body = draft if config.SYNTHETIC_DATA_BANNER in draft else f"**{config.SYNTHETIC_DATA_BANNER}**\n\n{draft}"
    body += (
        f"\n\n## Review Status\n\n"
        f"- **Decision:** {decision}\n"
        f"- **Reviewer note:** {reviewer_note or '(none)'}\n"
    )
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS_DIR / f"{state['run_id']}-{state['current_candidate_index']}-safety-report.md"
    path.write_text(body, encoding="utf-8")
    return path


async def finalize_node(state: PipelineState, mcp: MCPClient) -> dict:
    """The ONLY call site for record_signal_decision in the entire system
    (CLAUDE.md rule #4), and only reachable after human_approval_node's
    interrupt() has actually returned a resumed human decision."""
    candidate = state["candidates"][state["current_candidate_index"]]
    assert state["pending_decision"] is not None, "finalize_node requires a recorded human decision"
    decision_payload = json.loads(state["pending_decision"])
    decision = decision_payload["decision"]
    reviewer_note = decision_payload.get("reviewer_note", "")

    _write_report_file(state, candidate, decision, reviewer_note)

    result = await mcp.record_signal_decision(
        drug_name=candidate["drug_name"],
        event_name=candidate["event_name"],
        run_id=state["run_id"],
        decision=decision,
        reviewer_note=reviewer_note,
        prr_at_decision=candidate["prr"],
        case_count_at_decision=candidate["case_count"],
    )
    trace = state["trace"] + [
        StepRecord(
            agent="finalize",
            step_type="tool_result",
            content=json.dumps({"tool": "record_signal_decision", "result": result}, default=str),
            timestamp=_now(),
        )
    ]

    finalized = state["finalized"] + [
        {
            "drug_name": candidate["drug_name"],
            "event_name": candidate["event_name"],
            "decision": decision,
            "reviewer_note": reviewer_note,
            "report": state["current_report"],
        }
    ]
    next_index = state["current_candidate_index"] + 1

    snapshot = {**state, "trace": trace, "finalized": finalized, "current_candidate_index": next_index,
                "current_literature": None, "current_report": None, "pending_decision": None}
    save_state(snapshot)  # human-inspectable side artifact; not read back by the graph

    return {
        "trace": trace,
        "finalized": finalized,
        "current_candidate_index": next_index,
        "current_literature": None,
        "current_report": None,
        "pending_decision": None,
    }


def _route_after_signal_detector(state: PipelineState) -> str:
    return "literature_reviewer" if state["candidates"] else END


def _route_after_finalize(state: PipelineState) -> str:
    return "literature_reviewer" if state["current_candidate_index"] < len(state["candidates"]) else END


def build_graph(checkpointer, mcp: MCPClient):
    """Build the compiled pv-signal-detection graph against a given
    checkpointer (an AsyncSqliteSaver -- see agents/cli_test_run.py / the
    Streamlit app for how it's opened) and a connected MCPClient.
    """

    async def _signal_detector(state: PipelineState) -> dict:
        return await signal_detector_node(state, mcp)

    async def _literature_reviewer(state: PipelineState) -> dict:
        return await literature_reviewer_node(state, mcp)

    async def _safety_report_writer(state: PipelineState) -> dict:
        return await safety_report_writer_node(state, mcp)

    async def _finalize(state: PipelineState) -> dict:
        return await finalize_node(state, mcp)

    graph = StateGraph(PipelineState)
    graph.add_node("signal_detector", _signal_detector)
    graph.add_node("literature_reviewer", _literature_reviewer)
    graph.add_node("safety_report_writer", _safety_report_writer)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("finalize", _finalize)

    graph.add_edge(START, "signal_detector")
    graph.add_conditional_edges(
        "signal_detector", _route_after_signal_detector, {"literature_reviewer": "literature_reviewer", END: END}
    )
    graph.add_edge("literature_reviewer", "safety_report_writer")
    graph.add_edge("safety_report_writer", "human_approval")
    graph.add_edge("human_approval", "finalize")
    graph.add_conditional_edges(
        "finalize", _route_after_finalize, {"literature_reviewer": "literature_reviewer", END: END}
    )

    return graph.compile(checkpointer=checkpointer)
