"""Throwaway self-test for mcp_server/server.py, exercised ONLY through mcp_client/client.py
(never by importing server.py's functions directly), per Phase 2 instructions.

Builds a small throwaway sqlite fixture + literature corpus fixture (hand-verifiable
counts, not the real data/db.sqlite or data/literature_corpus.json -- those may not exist
yet, since Phase 1's data generator runs in parallel), points the server at them via the
PV_MCP_*_PATH env-var overrides, spawns the real server subprocess through MCPClient, and
round-trips every tool. Cleans up its own temp directory when done; never touches
data/db.sqlite, data/literature_corpus.json, or memory/signal_history.sqlite.

Run with: /home/labuser/venv/bin/python3 mcp_server/_test_tools.py
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_client.client import MCPClient  # noqa: E402

FIXTURE_DIR = Path(tempfile.mkdtemp(prefix="pv_mcp_test_"))
FIXTURE_DB_PATH = FIXTURE_DIR / "throwaway_db.sqlite"
FIXTURE_LIT_PATH = FIXTURE_DIR / "throwaway_literature_corpus.json"
FIXTURE_HISTORY_PATH = FIXTURE_DIR / "throwaway_signal_history.sqlite"

# Hand-verifiable fixture: 3 drugs x 3 events, 100 AE reports per drug (300 total).
#   Drugalin/Vertigo is a deliberately elevated pair (PRR = 6.0, see hand-derivation below).
#   */Headache is a deliberately flat/noise pair (PRR = 1.0 for Drugalin/Headache).
DRUGS = {
    "Drugalin": {"label_events": ["Nausea"], "counts": {"Vertigo": 30, "Headache": 40, "Nausea": 30}},
    "Placebexin": {"label_events": [], "counts": {"Vertigo": 5, "Headache": 40, "Nausea": 55}},
    "Neutrivan": {"label_events": [], "counts": {"Vertigo": 5, "Headache": 40, "Nausea": 55}},
}

# Hand-derived expected values for (Drugalin, Vertigo):
#   a=30 (Drugalin&Vertigo), b=70 (Drugalin&!Vertigo)
#   c=10 (!Drugalin&Vertigo = (5+5)), d=190 (!Drugalin&!Vertigo)
#   exposed_rate=30/100=0.3, background_rate=10/200=0.05, PRR=6.0
EXPECTED_ELEVATED = {"drug_name": "Drugalin", "event_name": "Vertigo", "case_count": 30, "a": 30, "b": 70, "c": 10, "d": 190, "prr": 6.0}
# Hand-derived expected values for (Drugalin, Headache): a=40,b=60,c=80,d=120 -> PRR=1.0 (noise)
EXPECTED_NOISE = {"drug_name": "Drugalin", "event_name": "Headache", "case_count": 40, "a": 40, "b": 60, "c": 80, "d": 120, "prr": 1.0}

LITERATURE_FIXTURE = {
    "publications": [
        {
            "id": "pub-001",
            "title": "Vertigo signal with Drugalin therapy: a case series",
            "drug_name": "Drugalin",
            "event_name": "Vertigo",
            "stance": "corroborates",
            "text": "Case series describing vertigo onset in patients treated with Drugalin.",
        },
        {
            "id": "pub-002",
            "title": "Population headache prevalence study",
            "drug_name": "Placebexin",
            "event_name": "Headache",
            "stance": "irrelevant",
            "text": "General population headache prevalence, no drug signal discussed here.",
        },
    ]
}


def build_fixture_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE drugs (drug_id INTEGER PRIMARY KEY, drug_name TEXT NOT NULL UNIQUE, "
            "label_events TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE ae_reports (report_id INTEGER PRIMARY KEY, "
            "drug_id INTEGER NOT NULL REFERENCES drugs(drug_id), event_name TEXT NOT NULL, "
            "patient_age INTEGER, patient_sex TEXT, report_date TEXT NOT NULL)"
        )
        report_date = 20240101
        for drug_name, spec in DRUGS.items():
            cur = conn.execute(
                "INSERT INTO drugs (drug_name, label_events) VALUES (?, ?)",
                (drug_name, json.dumps(spec["label_events"])),
            )
            drug_id = cur.lastrowid
            age = 20
            for event_name, count in spec["counts"].items():
                for i in range(count):
                    sex = "F" if i % 2 == 0 else "M"
                    age = 20 + (i % 50)
                    conn.execute(
                        "INSERT INTO ae_reports (drug_id, event_name, patient_age, patient_sex, report_date) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (drug_id, event_name, age, sex, str(report_date + i)),
                    )
        conn.commit()
    finally:
        conn.close()


def build_fixture_literature(path: Path) -> None:
    path.write_text(json.dumps(LITERATURE_FIXTURE), encoding="utf-8")


async def main() -> None:
    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(label)

    build_fixture_db(FIXTURE_DB_PATH)
    build_fixture_literature(FIXTURE_LIT_PATH)
    assert not FIXTURE_HISTORY_PATH.exists(), "history fixture must start absent"

    env = {
        "PV_MCP_DB_PATH": str(FIXTURE_DB_PATH),
        "PV_MCP_LITERATURE_CORPUS_PATH": str(FIXTURE_LIT_PATH),
        "PV_MCP_SIGNAL_HISTORY_DB_PATH": str(FIXTURE_HISTORY_PATH),
    }
    client = MCPClient(env=env)
    try:
        await client.connect()

        # --- query_ae_reports ---
        all_rows = await client.query_ae_reports(limit=1000)
        check("query_ae_reports: total row count == 300", len(all_rows) == 300, f"got {len(all_rows)}")
        check(
            "query_ae_reports: row shape has exactly the contracted fields",
            all_rows and set(all_rows[0].keys()) == {"report_id", "drug_name", "event_name", "patient_age", "patient_sex", "report_date"},
            str(all_rows[0].keys() if all_rows else None),
        )

        drugalin_rows = await client.query_ae_reports(drug_name="Drugalin", limit=1000)
        check("query_ae_reports: drug_name filter == 100 rows", len(drugalin_rows) == 100, f"got {len(drugalin_rows)}")
        check(
            "query_ae_reports: drug_name filter rows all say Drugalin",
            all(r["drug_name"] == "Drugalin" for r in drugalin_rows),
        )

        vertigo_rows = await client.query_ae_reports(event_name="Vertigo", limit=1000)
        check("query_ae_reports: event_name filter == 40 rows (30+5+5)", len(vertigo_rows) == 40, f"got {len(vertigo_rows)}")

        limited = await client.query_ae_reports(limit=5)
        check("query_ae_reports: limit=5 respected", len(limited) == 5, f"got {len(limited)}")

        # --- calculate_prr ---
        elevated = await client.calculate_prr("Drugalin", "Vertigo")
        check(
            "calculate_prr: elevated pair a/b/c/d match hand-derivation",
            (elevated["a"], elevated["b"], elevated["c"], elevated["d"]) == (30, 70, 10, 190),
            str(elevated),
        )
        check(
            "calculate_prr: elevated pair PRR == 6.0",
            math.isclose(elevated["prr"], 6.0, rel_tol=1e-9),
            str(elevated["prr"]),
        )
        check("calculate_prr: elevated pair case_count == 30", elevated["case_count"] == 30, str(elevated))

        noise = await client.calculate_prr("Drugalin", "Headache")
        check(
            "calculate_prr: noise pair a/b/c/d match hand-derivation",
            (noise["a"], noise["b"], noise["c"], noise["d"]) == (40, 60, 80, 120),
            str(noise),
        )
        check("calculate_prr: noise pair PRR == 1.0 (flat)", math.isclose(noise["prr"], 1.0, rel_tol=1e-9), str(noise["prr"]))

        unknown = await client.calculate_prr("NoSuchDrug", "Vertigo")
        check("calculate_prr: unknown drug does not error, case_count == 0", unknown["case_count"] == 0, str(unknown))

        # --- scan_signals ---
        scanned = await client.scan_signals(prr_threshold=2.0, min_cases=3)
        scanned_pairs = {(r["drug_name"], r["event_name"]): r for r in scanned}
        check(
            "scan_signals: elevated (Drugalin, Vertigo) present above threshold",
            ("Drugalin", "Vertigo") in scanned_pairs,
            str(list(scanned_pairs.keys())),
        )
        if ("Drugalin", "Vertigo") in scanned_pairs:
            row = scanned_pairs[("Drugalin", "Vertigo")]
            check("scan_signals: elevated pair PRR == 6.0", math.isclose(row["prr"], 6.0, rel_tol=1e-9), str(row))
            check(
                "scan_signals: elevated pair background_rate == 0.05",
                math.isclose(row["background_rate"], 0.05, rel_tol=1e-9),
                str(row),
            )
        check(
            "scan_signals: flat noise (Drugalin, Headache) correctly excluded (PRR==1.0 < threshold)",
            ("Drugalin", "Headache") not in scanned_pairs,
        )
        prrs = [r["prr"] for r in scanned]
        check("scan_signals: results sorted by PRR descending", prrs == sorted(prrs, reverse=True), str(prrs))
        check(
            "scan_signals: every returned pair meets min_cases",
            all(r["case_count"] >= 3 for r in scanned),
        )

        # --- get_drug_label ---
        label = await client.get_drug_label("Drugalin")
        check("get_drug_label: known drug returns its label_events", label == {"drug_name": "Drugalin", "label_events": ["Nausea"]}, str(label))
        no_label = await client.get_drug_label("NoSuchDrug")
        check("get_drug_label: unknown drug returns empty label_events, no error", no_label == {"drug_name": "NoSuchDrug", "label_events": []}, str(no_label))

        # --- search_literature ---
        hits = await client.search_literature("vertigo")
        check("search_literature: 'vertigo' matches exactly pub-001", [h["id"] for h in hits] == ["pub-001"], str(hits))
        hits_drug = await client.search_literature("drugalin")
        check("search_literature: 'drugalin' (drug_name field) matches pub-001", [h["id"] for h in hits_drug] == ["pub-001"], str(hits_drug))
        hits_case = await client.search_literature("VERTIGO")
        check("search_literature: case-insensitive match", [h["id"] for h in hits_case] == ["pub-001"], str(hits_case))
        hits_none = await client.search_literature("nonexistentxyzterm")
        check("search_literature: no match returns empty list, no error", hits_none == [], str(hits_none))

        # --- get_signal_history (before any decisions) ---
        empty_hist = await client.get_signal_history("Drugalin", "Vertigo")
        check(
            "get_signal_history: pair with no history returns empty history, first_seen_run_id None, no error",
            empty_hist == {"drug_name": "Drugalin", "event_name": "Vertigo", "first_seen_run_id": None, "history": []},
            str(empty_hist),
        )

        # --- record_signal_decision + get_signal_history round trip ---
        rec1 = await client.record_signal_decision(
            drug_name="Drugalin",
            event_name="Vertigo",
            run_id="run-001",
            decision="approved",
            reviewer_note="Confirmed elevated PRR, escalating.",
            prr_at_decision=6.0,
            case_count_at_decision=30,
        )
        check("record_signal_decision: returns success", rec1 == {"success": True}, str(rec1))

        hist_after_one = await client.get_signal_history("Drugalin", "Vertigo")
        check(
            "get_signal_history: after one decision, first_seen_run_id == run-001",
            hist_after_one["first_seen_run_id"] == "run-001",
            str(hist_after_one),
        )
        check("get_signal_history: after one decision, history has 1 entry", len(hist_after_one["history"]) == 1, str(hist_after_one))
        if hist_after_one["history"]:
            entry = hist_after_one["history"][0]
            check(
                "get_signal_history: recorded entry fields round-trip correctly",
                (entry["run_id"], entry["decision"], entry["reviewer_note"], entry["prr_at_decision"], entry["case_count_at_decision"])
                == ("run-001", "approved", "Confirmed elevated PRR, escalating.", 6.0, 30),
                str(entry),
            )

        rec2 = await client.record_signal_decision(
            drug_name="Drugalin",
            event_name="Vertigo",
            run_id="run-002",
            decision="sent_back",
            reviewer_note="Ask literature-reviewer for more corroboration.",
            prr_at_decision=6.0,
            case_count_at_decision=30,
        )
        check("record_signal_decision: second decision (different run_id) succeeds", rec2 == {"success": True}, str(rec2))

        hist_after_two = await client.get_signal_history("Drugalin", "Vertigo")
        check("get_signal_history: after two decisions, history has 2 entries", len(hist_after_two["history"]) == 2, str(hist_after_two))
        check(
            "get_signal_history: first_seen_run_id still run-001 (earliest by timestamp)",
            hist_after_two["first_seen_run_id"] == "run-001",
            str(hist_after_two),
        )

        # A different, never-decided pair must still report empty history (no bleed-over).
        other_pair_hist = await client.get_signal_history("Placebexin", "Vertigo")
        check(
            "get_signal_history: unrelated pair unaffected, still empty",
            other_pair_hist == {"drug_name": "Placebexin", "event_name": "Vertigo", "first_seen_run_id": None, "history": []},
            str(other_pair_hist),
        )

        check("record_signal_decision: created the throwaway history db file", FIXTURE_HISTORY_PATH.exists())

    finally:
        await client.close()
        shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
        print(f"\nCleaned up throwaway fixture dir: {FIXTURE_DIR}")

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
