"""MCP server exposing synthetic pharmacovigilance data/statistics/memory tools.

Real `mcp`-SDK server (mcp==2.2.0, `mcp.server.mcpserver.MCPServer`), run over stdio and
spawned as a subprocess by `mcp_client/client.py` -- never imported directly by agent/graph
code, so the MCP protocol is genuinely exercised (see ARCHITECTURE.md §5.2, CLAUDE.md
coding standards).

Tools (exact signatures fixed by ARCHITECTURE.md §6.1):
    query_ae_reports, scan_signals, calculate_prr, get_drug_label,
    search_literature, get_signal_history, record_signal_decision

Statistics (PRR / disproportionality) are computed here, deterministically, in plain
Python/SQL -- never delegated to an LLM (CLAUDE.md business rule #3).

Testability note: the three data paths below default to `config.py`'s constants exactly
(production behavior is unchanged) but may be overridden via the PV_MCP_DB_PATH /
PV_MCP_LITERATURE_CORPUS_PATH / PV_MCP_SIGNAL_HISTORY_DB_PATH environment variables. This
exists solely so `mcp_server/_test_tools.py` can round-trip this server (spawned as a real
subprocess, over real stdio) against a throwaway fixture without ever touching the real
`data/db.sqlite`, `data/literature_corpus.json`, or `memory/signal_history.sqlite` -- which
may not exist yet (Phase 1's data generator runs in parallel) or may already be the real
pipeline's files. No tool signature or schema changes as a result.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
from pathlib import Path

# Guarantee the project root is importable regardless of the cwd this server is spawned
# from (mcp_client spawns it as a subprocess over stdio; we don't control its cwd).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (must follow the sys.path fix-up above)

from mcp.server.mcpserver import MCPServer  # noqa: E402

# --- Data paths (see module docstring re: test-only env overrides) ---
DB_PATH = Path(os.environ["PV_MCP_DB_PATH"]) if os.environ.get("PV_MCP_DB_PATH") else config.DB_PATH
LITERATURE_CORPUS_PATH = (
    Path(os.environ["PV_MCP_LITERATURE_CORPUS_PATH"])
    if os.environ.get("PV_MCP_LITERATURE_CORPUS_PATH")
    else config.LITERATURE_CORPUS_PATH
)
SIGNAL_HISTORY_DB_PATH = (
    Path(os.environ["PV_MCP_SIGNAL_HISTORY_DB_PATH"])
    if os.environ.get("PV_MCP_SIGNAL_HISTORY_DB_PATH")
    else config.SIGNAL_HISTORY_DB_PATH
)

SIGNAL_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_history (
    drug_name TEXT NOT NULL,
    event_name TEXT NOT NULL,
    run_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer_note TEXT,
    prr_at_decision REAL,
    case_count_at_decision INTEGER,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (drug_name, event_name, run_id)
)
"""

server = MCPServer(
    name="pv-mcp-server",
    instructions=(
        "Synthetic pharmacovigilance signal-detection tools: AE report queries, "
        "server-side PRR disproportionality analysis, a synthetic literature corpus "
        "search, drug label lookups, and cross-run signal review history. "
        "All data is synthetic -- SYNTHETIC DEMO, NOT REAL DATA."
    ),
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@server.tool()
def query_ae_reports(
    drug_name: str | None = None,
    event_name: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return raw synthetic AE report rows, optionally filtered by drug and/or event name."""
    conn = _connect(DB_PATH)
    try:
        query = (
            "SELECT r.report_id AS report_id, d.drug_name AS drug_name, "
            "r.event_name AS event_name, r.patient_age AS patient_age, "
            "r.patient_sex AS patient_sex, r.report_date AS report_date "
            "FROM ae_reports r JOIN drugs d ON r.drug_id = d.drug_id"
        )
        clauses = []
        params: list = []
        if drug_name is not None:
            clauses.append("d.drug_name = ?")
            params.append(drug_name)
        if event_name is not None:
            clauses.append("r.event_name = ?")
            params.append(event_name)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY r.report_id LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@server.tool()
def scan_signals(prr_threshold: float = 2.0, min_cases: int = 3) -> list[dict]:
    """Server-side disproportionality scan.

    Computes PRR for every (drug, event) pair in one pass over a grouped SQL query
    (not one query per candidate pair), using the fixed 2x2 contingency table:
        a = count(drug=X, event=Y)      b = count(drug=X, event!=Y)
        c = count(drug!=X, event=Y)     d = count(drug!=X, event!=Y)
        PRR = (a / (a+b)) / (c / (c+d))
    Returns only pairs meeting both `prr_threshold` and `min_cases`, sorted by PRR desc.
    """
    conn = _connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT d.drug_name AS drug_name, r.event_name AS event_name, COUNT(*) AS cnt "
            "FROM ae_reports r JOIN drugs d ON r.drug_id = d.drug_id "
            "GROUP BY d.drug_name, r.event_name"
        ).fetchall()
    finally:
        conn.close()

    pair_counts: dict[tuple[str, str], int] = {}
    drug_totals: dict[str, int] = {}
    event_totals: dict[str, int] = {}
    total = 0
    for row in rows:
        drug, event, cnt = row["drug_name"], row["event_name"], row["cnt"]
        pair_counts[(drug, event)] = cnt
        drug_totals[drug] = drug_totals.get(drug, 0) + cnt
        event_totals[event] = event_totals.get(event, 0) + cnt
        total += cnt

    results = []
    for (drug, event), a in pair_counts.items():
        drug_total = drug_totals[drug]
        event_total = event_totals[event]
        b = drug_total - a
        c = event_total - a
        d = total - drug_total - event_total + a
        if (a + b) == 0 or (c + d) == 0 or c == 0:
            # No comparison group, or the event never occurs off-drug: PRR isn't a
            # finite ratio. Skip rather than emit inf/NaN over the wire.
            continue
        exposed_rate = a / (a + b)
        background_rate = c / (c + d)
        prr = exposed_rate / background_rate
        if a >= min_cases and prr >= prr_threshold:
            results.append(
                {
                    "drug_name": drug,
                    "event_name": event,
                    "case_count": a,
                    "prr": prr,
                    "background_rate": background_rate,
                }
            )
    results.sort(key=lambda r: r["prr"], reverse=True)
    return results


@server.tool()
def calculate_prr(drug_name: str, event_name: str) -> dict:
    """On-demand PRR lookup for a single drug-event pair (same formula as `scan_signals`)."""
    conn = _connect(DB_PATH)
    try:
        drug_row = conn.execute(
            "SELECT drug_id FROM drugs WHERE drug_name = ?", (drug_name,)
        ).fetchone()
        drug_id = drug_row["drug_id"] if drug_row else None

        total = conn.execute("SELECT COUNT(*) AS n FROM ae_reports").fetchone()["n"]

        if drug_id is None:
            a = 0
            drug_total = 0
        else:
            a = conn.execute(
                "SELECT COUNT(*) AS n FROM ae_reports WHERE drug_id = ? AND event_name = ?",
                (drug_id, event_name),
            ).fetchone()["n"]
            drug_total = conn.execute(
                "SELECT COUNT(*) AS n FROM ae_reports WHERE drug_id = ?", (drug_id,)
            ).fetchone()["n"]

        event_total = conn.execute(
            "SELECT COUNT(*) AS n FROM ae_reports WHERE event_name = ?", (event_name,)
        ).fetchone()["n"]
    finally:
        conn.close()

    b = drug_total - a
    c = event_total - a
    d = total - drug_total - event_total + a

    prr = 0.0
    if (a + b) > 0 and (c + d) > 0 and c > 0:
        prr = (a / (a + b)) / (c / (c + d))

    return {
        "drug_name": drug_name,
        "event_name": event_name,
        "case_count": a,
        "prr": prr,
        "a": a,
        "b": b,
        "c": c,
        "d": d,
    }


@server.tool()
def get_drug_label(drug_name: str) -> dict:
    """Return the synthetic label events already documented for a drug."""
    conn = _connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT label_events FROM drugs WHERE drug_name = ?", (drug_name,)
        ).fetchone()
    finally:
        conn.close()

    if row and row["label_events"]:
        label_events = json.loads(row["label_events"])
    else:
        label_events = []

    return {"drug_name": drug_name, "label_events": label_events}


@server.tool()
def search_literature(query: str) -> list[dict]:
    """Case-insensitive keyword search over `title`, `text`, `drug_name`, `event_name`
    fields of the synthetic literature corpus. No live web search (CLAUDE.md rule #2)."""
    with open(LITERATURE_CORPUS_PATH, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    needle = query.lower()
    matches = []
    for pub in corpus.get("publications", []):
        haystack = " ".join(
            str(pub.get(field, "")) for field in ("title", "text", "drug_name", "event_name")
        ).lower()
        if needle in haystack:
            matches.append(
                {
                    "id": pub.get("id"),
                    "title": pub.get("title"),
                    "drug_name": pub.get("drug_name"),
                    "event_name": pub.get("event_name"),
                    "stance": pub.get("stance"),
                    "text": pub.get("text"),
                }
            )
    return matches


@server.tool()
def get_signal_history(drug_name: str, event_name: str) -> dict:
    """Read-only long-term memory lookup: cross-run review history for a drug-event pair.

    Never errors if the pair (or the database itself) has no history yet -- returns an
    empty history and `first_seen_run_id: None` in that case.
    """
    history: list[dict] = []
    if SIGNAL_HISTORY_DB_PATH.exists():
        conn = _connect(SIGNAL_HISTORY_DB_PATH)
        try:
            cur = conn.execute(
                "SELECT run_id, decision, reviewer_note, prr_at_decision, "
                "case_count_at_decision, timestamp FROM signal_history "
                "WHERE drug_name = ? AND event_name = ? ORDER BY timestamp ASC",
                (drug_name, event_name),
            )
            history = [dict(row) for row in cur.fetchall()]
        except sqlite3.OperationalError:
            # Table doesn't exist yet -- no history recorded at all so far.
            history = []
        finally:
            conn.close()

    first_seen_run_id = history[0]["run_id"] if history else None
    return {
        "drug_name": drug_name,
        "event_name": event_name,
        "first_seen_run_id": first_seen_run_id,
        "history": history,
    }


@server.tool()
def record_signal_decision(
    drug_name: str,
    event_name: str,
    run_id: str,
    decision: str,
    reviewer_note: str,
    prr_at_decision: float,
    case_count_at_decision: int,
) -> dict:
    """Write a human review decision to long-term memory, creating the database/table
    if needed. NO caller restriction is enforced at this layer -- gating this to only be
    called by the `finalize` graph node post-approval is a separate, later guardrail
    (CLAUDE.md business rule #4; ARCHITECTURE.md §5.4)."""
    SIGNAL_HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(SIGNAL_HISTORY_DB_PATH)
    try:
        conn.execute(SIGNAL_HISTORY_SCHEMA)
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO signal_history
                (drug_name, event_name, run_id, decision, reviewer_note,
                 prr_at_decision, case_count_at_decision, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drug_name, event_name, run_id) DO UPDATE SET
                decision = excluded.decision,
                reviewer_note = excluded.reviewer_note,
                prr_at_decision = excluded.prr_at_decision,
                case_count_at_decision = excluded.case_count_at_decision,
                timestamp = excluded.timestamp
            """,
            (
                drug_name,
                event_name,
                run_id,
                decision,
                reviewer_note,
                prr_at_decision,
                case_count_at_decision,
                timestamp,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {"success": True}


if __name__ == "__main__":
    server.run()
