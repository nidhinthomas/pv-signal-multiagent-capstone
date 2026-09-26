"""Streamlit front-end for the pv-signal-detection pipeline (Phase 6).

Implementation-only against the fixed spec in ARCHITECTURE.md §7 (palette,
layout, tab contents) -- no redesign happens here.

Lifecycle note (ARCHITECTURE.md §2/§6, PLAN.md point 5): the compiled
LangGraph graph and the MCPClient session are async, but Streamlit reruns
this whole script synchronously on every interaction. Both are built once
per server process via `st.cache_resource`, and every coroutine against
them runs on one persistent background event loop (also `st.cache_resource`)
rather than a fresh `asyncio.run()` per rerun -- a fresh loop per rerun
would tear down and respawn the MCP stdio subprocess and the sqlite
checkpointer connection on every click. Long-running calls (`graph.ainvoke`,
which drives a full literature-review/report-drafting LLM cycle) are
submitted to that loop without blocking Streamlit's server thread; a
`st.fragment(run_every=...)` polls for completion so the UI stays responsive
and refreshes automatically instead of freezing on a spinner.
"""

from __future__ import annotations

import asyncio
import datetime
import html
import json
import sys
import threading
from pathlib import Path
from typing import Any, Optional

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from agents.graph import build_graph
from agents.state import new_state
from mcp_client.client import MCPClient

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

# --- Palette (ARCHITECTURE.md §7.1 -- fixed, do not improvise new colors) --

BG_LIGHT, TEXT_PRIMARY = "#F7F7F5", "#1A1B1E"
ACCENT = {
    "signal-detector": "#4C5FD5",
    "literature-reviewer": "#1E9E8C",
    "safety-report-writer": "#D68A2B",
    "finalize": "#8B5CF6",  # shares the human-approval accent (violet)
}
STATUS_COLOR = {
    "Approved": "#2E9E5B",
    "Rejected": "#D64545",
    "Sent back": "#D64545",
    "Pending": "#8A8D93",
    "Awaiting approval": "#8A8D93",
    "Recurring/Known": "#D6672B",
    "Complete": "#2E9E5B",
    "Running": "#8A8D93",
}
BANNER_BG, BANNER_TEXT = "#3A2E12", "#F5C563"
STEP_ICON = {"reasoning": "🧠", "tool_call": "🔧", "tool_result": "📊", "output": "📝"}

st.set_page_config(page_title="PV Signal Detection (Synthetic Demo)", layout="wide")

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: {BG_LIGHT}; color: {TEXT_PRIMARY}; }}
    .synthetic-banner {{
        background-color: {BANNER_BG}; color: {BANNER_TEXT};
        padding: 10px 16px; border-radius: 6px; font-weight: 700;
        text-align: center; margin-bottom: 1rem; letter-spacing: 0.02em;
    }}
    .trace-card {{
        border-left: 5px solid #8A8D93; padding: 8px 14px; margin-bottom: 8px;
        background-color: rgba(0,0,0,0.03); border-radius: 4px;
    }}
    .status-pill {{
        display: inline-block; padding: 2px 10px; border-radius: 12px;
        color: white; font-size: 0.8em; font-weight: 600;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


def banner() -> None:
    st.markdown(
        f'<div class="synthetic-banner">⚠️ {config.SYNTHETIC_DATA_BANNER} ⚠️</div>',
        unsafe_allow_html=True,
    )


def pill(label: str) -> str:
    color = STATUS_COLOR.get(label, "#8A8D93")
    return f'<span class="status-pill" style="background-color:{color}">{label}</span>'


# --- Persistent background event loop + cached async resources -------------


@st.cache_resource
def get_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run_async(coro, timeout: float = 30.0):
    """Block the current (Streamlit) thread for a short, cheap coroutine
    (e.g. a checkpointer read) -- never for a full pipeline invocation."""
    return asyncio.run_coroutine_threadsafe(coro, get_loop()).result(timeout=timeout)


def submit_async(coro):
    """Fire-and-track a long-running coroutine (a full graph.ainvoke step)
    without blocking Streamlit's server thread. Returns a concurrent.futures
    Future the UI polls via st.fragment."""
    return asyncio.run_coroutine_threadsafe(coro, get_loop())


@st.cache_resource(show_spinner="Connecting to MCP server and building the pipeline graph...")
def get_pipeline():
    async def _setup():
        mcp = MCPClient()
        await mcp.connect()
        config.CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        saver_cm = AsyncSqliteSaver.from_conn_string(str(config.CHECKPOINT_DB_PATH))
        checkpointer = await saver_cm.__aenter__()  # kept open for the server process's lifetime
        graph = build_graph(checkpointer, mcp)
        # saver_cm itself must be kept alive for the process lifetime, not just
        # the checkpointer it yielded: it's an @asynccontextmanager-backed
        # generator, and if the generator object is garbage-collected while
        # nothing external references it, Python finalizes it (runs its
        # `finally` cleanup, closing the sqlite connection) even though
        # __aexit__ was never called. Returning it here keeps a strong
        # reference alive as long as st.cache_resource holds this tuple.
        return graph, mcp, saver_cm

    return run_async(_setup())


def thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def get_state_snapshot(run_id: str) -> dict:
    graph, _mcp, _saver_cm = get_pipeline()
    snap = run_async(graph.aget_state(thread_config(run_id)))
    interrupt_payload = None
    for task in snap.tasks:
        if task.interrupts:
            interrupt_payload = task.interrupts[0].value
            break
    return {"values": snap.values or {}, "next": snap.next, "interrupt": interrupt_payload}


def status_pill_label(snap: dict) -> str:
    values = snap["values"]
    if not values:
        return "Pending"
    if snap["interrupt"]:
        return "Awaiting approval"
    candidates = values.get("candidates", [])
    if candidates and values.get("current_candidate_index", 0) >= len(candidates):
        return "Complete"
    return "Running"


# --- Session state -----------------------------------------------------------

st.session_state.setdefault("known_runs", [])
st.session_state.setdefault("futures", {})
st.session_state.setdefault("errors", {})
st.session_state.setdefault("active_run_id", None)


def known_run_ids() -> list[str]:
    on_disk = {p.name for p in config.RUNS_DIR.iterdir() if p.is_dir()} if config.RUNS_DIR.exists() else set()
    return sorted(set(st.session_state.known_runs) | on_disk)


# --- Sidebar: run controls + history ----------------------------------------

banner()
st.sidebar.markdown(f'<div class="synthetic-banner" style="font-size:0.75em">{config.SYNTHETIC_DATA_BANNER}</div>', unsafe_allow_html=True)
st.sidebar.header("Run Controls")

custom_id = st.sidebar.text_input("Run ID (optional)", placeholder="auto-generated if blank")
if st.sidebar.button("▶️ Start New Run", type="primary", use_container_width=True):
    run_id = custom_id.strip() or f"run-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    graph, _mcp, _saver_cm = get_pipeline()
    existing = run_async(graph.aget_state(thread_config(run_id)))
    if existing.values:
        st.sidebar.error(f"Run {run_id!r} already has state -- pick a different Run ID.")
    else:
        coro = graph.ainvoke(new_state(run_id), config=thread_config(run_id))
        st.session_state.futures[run_id] = submit_async(coro)
        if run_id not in st.session_state.known_runs:
            st.session_state.known_runs.append(run_id)
        st.session_state.active_run_id = run_id
        st.rerun()

st.sidebar.header("Run History")
run_ids = known_run_ids()
if not run_ids:
    st.sidebar.caption("No runs yet.")
for rid in run_ids:
    snap = get_state_snapshot(rid)
    label = status_pill_label(snap)
    marker = "👉 " if rid == st.session_state.active_run_id else ""
    cols = st.sidebar.columns([3, 2])
    with cols[0]:
        if st.button(f"{marker}{rid}", key=f"select_{rid}", use_container_width=True):
            st.session_state.active_run_id = rid
            st.rerun()
    with cols[1]:
        st.markdown(pill(label), unsafe_allow_html=True)

# --- Main-area tab renderers --------------------------------------------------


def render_live_run(run_id: str, snap: dict) -> None:
    trace = snap["values"].get("trace", [])
    if not trace:
        st.caption("No trace recorded yet for this run.")
        return
    for step in trace:
        agent, step_type, content = step["agent"], step["step_type"], step["content"]
        color = ACCENT.get(agent, "#8A8D93")
        icon = STEP_ICON.get(step_type, "•")
        display = content if len(content) <= 600 else content[:600] + " ..."
        st.markdown(
            f'<div class="trace-card" style="border-left-color:{color}">'
            f'<b>{icon} {agent}</b> <code>{step_type}</code><br>'
            f'<span style="white-space:pre-wrap">{html.escape(display)}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )


def render_draft_report(run_id: str, snap: dict) -> None:
    values = snap["values"]
    if not values:
        st.info("This run hasn't started yet.")
        return

    if snap["interrupt"]:
        payload = snap["interrupt"]
        st.markdown(
            f"**{html.escape(payload['drug_name'])}** / **{html.escape(payload['event_name'])}** "
            f"— PRR {payload['prr']:.2f}, {payload['case_count']} cases — "
            f"{pill('Awaiting approval')}",
            unsafe_allow_html=True,
        )
        st.caption(f"History status: {payload['history_status']}")
        st.markdown("---")
        st.markdown(payload["report"])
        st.markdown("---")
        note = st.text_area("Reviewer note", key=f"note_{run_id}")
        busy = run_id in st.session_state.futures
        c1, c2, c3 = st.columns(3)
        if c1.button("✅ Approve", type="primary", disabled=busy, use_container_width=True, key=f"approve_{run_id}"):
            _submit_decision(run_id, "approved", note)
        if c2.button("❌ Reject", disabled=busy, use_container_width=True, key=f"reject_{run_id}"):
            _submit_decision(run_id, "rejected", note)
        if c3.button("🔁 Send back", disabled=busy, use_container_width=True, key=f"sendback_{run_id}"):
            _submit_decision(run_id, "sent_back", note)
        return

    candidates = values.get("candidates", [])
    if candidates and values.get("current_candidate_index", 0) >= len(candidates):
        st.success(f"Run complete — {len(values.get('finalized', []))} candidate(s) finalized.")
        for f in values.get("finalized", []):
            decision_label = {"approved": "Approved", "rejected": "Rejected", "sent_back": "Sent back"}.get(
                f["decision"], f["decision"]
            )
            st.markdown(
                f"- **{f['drug_name']} / {f['event_name']}** — {pill(decision_label)}"
                + (f" — _{f['reviewer_note']}_" if f.get("reviewer_note") else ""),
                unsafe_allow_html=True,
            )
        return

    st.info("No draft report pending right now — the pipeline is between steps.")


def _submit_decision(run_id: str, decision: str, note: str) -> None:
    graph, _mcp, _saver_cm = get_pipeline()
    coro = graph.ainvoke(Command(resume={"decision": decision, "reviewer_note": note}), config=thread_config(run_id))
    st.session_state.futures[run_id] = submit_async(coro)
    st.rerun()


def render_evaluation() -> None:
    results_path = config.ROOT_DIR / "eval" / "eval_results.md"
    if results_path.exists():
        st.markdown(results_path.read_text(encoding="utf-8"))
    else:
        st.info("No evaluation results yet — run `eval/evaluate.py` (Phase 8) to generate `eval/eval_results.md`.")


def render_observability() -> None:
    if not config.OBSERVABILITY_LOG_PATH.exists():
        st.info("No observability log yet — run the pipeline at least once.")
        return
    rows = []
    for line in config.OBSERVABILITY_LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        st.info("Observability log is empty.")
        return

    agents = sorted({r.get("agent") for r in rows if r.get("agent")})
    tools = sorted({r.get("tool") for r in rows if r.get("tool")})
    runs = sorted({r.get("run_id") for r in rows if r.get("run_id")})
    c1, c2, c3 = st.columns(3)
    agent_filter = c1.multiselect("Agent", agents)
    tool_filter = c2.multiselect("Tool", tools)
    run_filter = c3.multiselect("Run", runs)

    filtered = [
        r
        for r in rows
        if (not agent_filter or r.get("agent") in agent_filter)
        and (not tool_filter or r.get("tool") in tool_filter)
        and (not run_filter or r.get("run_id") in run_filter)
    ]
    st.caption(f"{len(filtered)} / {len(rows)} events")
    st.dataframe(filtered, use_container_width=True, height=500)


def render_tabs(run_id: str, snap: dict) -> None:
    tab1, tab2, tab3, tab4 = st.tabs(["Live Run", "Draft Report", "Evaluation", "Observability"])
    with tab1:
        render_live_run(run_id, snap)
    with tab2:
        render_draft_report(run_id, snap)
    with tab3:
        render_evaluation()
    with tab4:
        render_observability()


# --- Main area ---------------------------------------------------------------

active_run_id = st.session_state.active_run_id
if active_run_id is None:
    st.info("Start a run from the sidebar to begin.")
else:
    err = st.session_state.errors.pop(active_run_id, None)
    if err:
        st.error(f"Pipeline error on run {active_run_id!r}: {err}")

    if active_run_id in st.session_state.futures:

        @st.fragment(run_every="2s")
        def _live_fragment(run_id: str = active_run_id) -> None:
            fut = st.session_state.futures.get(run_id)
            if fut is not None and fut.done():
                del st.session_state.futures[run_id]
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001 -- surfaced to the UI, not swallowed
                    st.session_state.errors[run_id] = str(exc)
                st.rerun()
            else:
                st.info("⏳ Pipeline step running in the background — this tab refreshes automatically every 2s.")
            render_tabs(run_id, get_state_snapshot(run_id))

        _live_fragment()
    else:
        render_tabs(active_run_id, get_state_snapshot(active_run_id))
