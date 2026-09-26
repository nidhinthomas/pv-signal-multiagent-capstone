"""Generate synthetic pharmacovigilance data for the capstone demo.

SYNTHETIC DEMO -- NOT REAL DATA. All drug names and adverse-event terms
below are fictional or generic placeholders invented for this exercise.
No real patient data, no real FAERS data, and no real branded drug names
are used anywhere in this script or its output.

Produces, matching the fixed schema in ARCHITECTURE.md section 6.1:
  - data/db.sqlite                    (drugs, ae_reports tables)
  - data/ground_truth_signals.json    (answer key for injected pairs)
  - data/literature_corpus.json       (fictional publication corpus)

Design approach
----------------
Rather than simulate individual reports with pure randomness and hope the
resulting PRR values land where we want, we build an explicit
drug x event count matrix:

  1. A background/independence model gives every (drug, event) cell an
     expected count proportional to that drug's overall report volume
     and that event's overall background frequency, with a small bounded
     random jitter -- this yields PRR values clustered near 1.0 (noise).
  2. A closed-form solve then overrides four specific cells (2 strong
     signals, 1 borderline signal, 1 label-known "trap case") to hit
     exact target PRR values. Because PRR's "d" term (reports for a
     different drug AND a different event) never includes the cell being
     overridden, d is invariant to that cell's value, which makes the
     PRR-vs-cell-count relationship solvable in closed form:

         PRR(x) = [x / (x + b0)] / [c0 / (c0 + d_fixed)]

     where b0 = rest of that drug's row, c0 = rest of that event's
     column, and d_fixed = grand total minus that whole row and column
     (all computed from the untouched background matrix). Solving for x
     given a target PRR is then simple algebra (see `solve_case_count`).

  3. ae_reports rows are then materialized directly from the final
     matrix, so the PRR values reported by the exit check below are
     exact restatements of what was engineered, not approximations.

Fixed random seed (RANDOM_SEED) makes every run reproducible.
"""

import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402  (after sys.path tweak)

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

DATA_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Fictional drug catalog. label_events = events already on this synthetic
# drug's label (used to distinguish a "trap case" -- statistically elevated
# but already known -- from a genuinely novel signal).
# ---------------------------------------------------------------------------
DRUGS = [
    {"name": "Neuroclarin", "weight": 0.12, "label_events": ["Nasal Congestion", "Insomnia"]},
    {"name": "Vastocor", "weight": 0.11, "label_events": ["Myalgia"]},
    {"name": "Ferinox", "weight": 0.10, "label_events": ["Xerostomia"]},
    {"name": "Cardiozan", "weight": 0.11, "label_events": ["QT Interval Prolongation", "Bradycardia"]},
    {"name": "Pulmivex", "weight": 0.09, "label_events": ["Cognitive Fog"]},
    {"name": "Renotrilax", "weight": 0.09, "label_events": ["Hyperkalemia"]},
    {"name": "Dermaquil", "weight": 0.08, "label_events": ["Pruritus"]},
    {"name": "Glucostabil", "weight": 0.10, "label_events": ["Weight Gain"]},
    {"name": "Hepacurin", "weight": 0.10, "label_events": ["Metallic Taste"]},
    {"name": "Osteofran", "weight": 0.10, "label_events": ["Orthostatic Hypotension"]},
]
assert abs(sum(d["weight"] for d in DRUGS) - 1.0) < 1e-9

# ---------------------------------------------------------------------------
# Fictional/generic adverse-event terms with background frequency weights
# (relative prevalence across the whole synthetic population, independent
# of drug -- this is what "noise" pairs are generated against).
# ---------------------------------------------------------------------------
EVENTS = [
    {"name": "Vertigo", "weight": 0.08},
    {"name": "Hepatic Enzyme Elevation", "weight": 0.05},
    {"name": "Photosensitivity Rash", "weight": 0.04},
    {"name": "QT Interval Prolongation", "weight": 0.03},
    {"name": "Peripheral Neuropathy", "weight": 0.05},
    {"name": "Hyperkalemia", "weight": 0.04},
    {"name": "Insomnia", "weight": 0.07},
    {"name": "Bradycardia", "weight": 0.04},
    {"name": "Tendon Rupture", "weight": 0.03},
    {"name": "Metallic Taste", "weight": 0.06},
    {"name": "Pruritus", "weight": 0.07},
    {"name": "Hypoglycemia", "weight": 0.05},
    {"name": "Nasal Congestion", "weight": 0.08},
    {"name": "Myalgia", "weight": 0.07},
    {"name": "Cognitive Fog", "weight": 0.06},
    {"name": "Orthostatic Hypotension", "weight": 0.04},
    {"name": "Weight Gain", "weight": 0.07},
    {"name": "Xerostomia", "weight": 0.07},
]
assert abs(sum(e["weight"] for e in EVENTS) - 1.0) < 1e-9

DRUG_NAMES = [d["name"] for d in DRUGS]
EVENT_NAMES = [e["name"] for e in EVENTS]
DRUG_WEIGHT = {d["name"]: d["weight"] for d in DRUGS}
EVENT_WEIGHT = {e["name"]: e["weight"] for e in EVENTS}

TOTAL_BASELINE_REPORTS = 10_000  # before engineered-pair overrides shift the total slightly

# ---------------------------------------------------------------------------
# Deliberately engineered (drug, event) pairs: strong / borderline / trap.
# Target PRRs chosen to satisfy the exit-check bands:
#   strong     -> PRR > 4  (well above PRR_THRESHOLD=2.0), margin comfortable
#   borderline -> PRR in ~1.8-2.5, a genuine close call
#   trap case  -> PRR > threshold AND event already in that drug's label_events
# ---------------------------------------------------------------------------
ENGINEERED_PAIRS = [
    {"drug_name": "Neuroclarin", "event_name": "Hepatic Enzyme Elevation", "strength": "strong", "is_trap_case": False, "target_prr": 6.0},
    {"drug_name": "Vastocor", "event_name": "Tendon Rupture", "strength": "strong", "is_trap_case": False, "target_prr": 5.0},
    {"drug_name": "Ferinox", "event_name": "Photosensitivity Rash", "strength": "borderline", "is_trap_case": False, "target_prr": 2.1},
    {"drug_name": "Cardiozan", "event_name": "QT Interval Prolongation", "strength": "strong", "is_trap_case": True, "target_prr": 4.5},
]
# Sanity: every trap case's event must already be on that drug's label.
for _pair in ENGINEERED_PAIRS:
    if _pair["is_trap_case"]:
        _drug = next(d for d in DRUGS if d["name"] == _pair["drug_name"])
        assert _pair["event_name"] in _drug["label_events"], "trap case event must be on the drug's label"
# Sanity: engineered pairs use distinct drugs and distinct events, so their
# row/column solves below don't interact with each other.
assert len({p["drug_name"] for p in ENGINEERED_PAIRS}) == len(ENGINEERED_PAIRS)
assert len({p["event_name"] for p in ENGINEERED_PAIRS}) == len(ENGINEERED_PAIRS)


def build_background_matrix() -> dict:
    """Independence-model baseline: count[(drug, event)] ~ N * dw * ew * jitter."""
    matrix = {}
    for d in DRUG_NAMES:
        for e in EVENT_NAMES:
            expected = TOTAL_BASELINE_REPORTS * DRUG_WEIGHT[d] * EVENT_WEIGHT[e]
            jitter = random.uniform(0.85, 1.15)
            matrix[(d, e)] = max(1, round(expected * jitter))
    return matrix


def solve_case_count(matrix: dict, drug_name: str, event_name: str, target_prr: float) -> int:
    """Closed-form solve for the cell count x that makes PRR(drug, event) == target_prr,
    holding every other cell in the background matrix fixed.

    a = x
    b0 = rest of drug_name's row (all other events for this drug)
    c0 = rest of event_name's column (all other drugs for this event)
    d_fixed = grand total of every cell NOT in this row and NOT in this column
              (invariant to x, since x only ever lives in this one cell)

    PRR(x) = [x / (x + b0)] / [c0 / (c0 + d_fixed)]
    Solving for x:
        let K = c0 / (c0 + d_fixed)
        target = [x / (x + b0)] / K
        x = target * K * b0 / (1 - target * K)
    """
    b0 = sum(count for (d, e), count in matrix.items() if d == drug_name and e != event_name)
    c0 = sum(count for (d, e), count in matrix.items() if e == event_name and d != drug_name)
    grand_total = sum(matrix.values())
    row_total_other = b0
    col_total_other = c0
    cell_current = matrix[(drug_name, event_name)]
    d_fixed = grand_total - cell_current - row_total_other - col_total_other

    k = c0 / (c0 + d_fixed)
    denom = 1 - target_prr * k
    if denom <= 0:
        raise ValueError(f"target_prr={target_prr} unreachable for {drug_name}/{event_name} (K={k})")
    x = target_prr * k * b0 / denom
    return max(1, round(x))


def compute_prr(matrix: dict, drug_name: str, event_name: str) -> dict:
    """Recompute the exact 2x2 contingency table + PRR from the final matrix."""
    a = matrix[(drug_name, event_name)]
    b = sum(count for (d, e), count in matrix.items() if d == drug_name and e != event_name)
    c = sum(count for (d, e), count in matrix.items() if e == event_name and d != drug_name)
    total = sum(matrix.values())
    dd = total - a - b - c
    prr = (a / (a + b)) / (c / (c + dd))
    return {"case_count": a, "prr": prr, "a": a, "b": b, "c": c, "d": dd}


def build_final_matrix() -> dict:
    matrix = build_background_matrix()
    for pair in ENGINEERED_PAIRS:
        x = solve_case_count(matrix, pair["drug_name"], pair["event_name"], pair["target_prr"])
        matrix[(pair["drug_name"], pair["event_name"])] = x
    return matrix


def generate_reports(matrix: dict) -> list:
    """Materialize ae_reports rows from the final count matrix."""
    reports = []
    start_date = date(2022, 1, 1)
    span_days = (date(2025, 12, 31) - start_date).days
    for (drug_name, event_name), count in matrix.items():
        for _ in range(count):
            age = random.randint(18, 90)
            sex = random.choice(["M", "F"])
            report_date = start_date + timedelta(days=random.randint(0, span_days))
            reports.append(
                {
                    "drug_name": drug_name,
                    "event_name": event_name,
                    "patient_age": age,
                    "patient_sex": sex,
                    "report_date": report_date.isoformat(),
                }
            )
    random.shuffle(reports)
    return reports


def write_db(reports: list) -> None:
    db_path = config.DB_PATH
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE drugs (
            drug_id INTEGER PRIMARY KEY,
            drug_name TEXT NOT NULL UNIQUE,
            label_events TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE ae_reports (
            report_id INTEGER PRIMARY KEY,
            drug_id INTEGER NOT NULL REFERENCES drugs(drug_id),
            event_name TEXT NOT NULL,
            patient_age INTEGER,
            patient_sex TEXT,
            report_date TEXT NOT NULL
        )
        """
    )

    drug_id_by_name = {}
    for d in DRUGS:
        cur.execute(
            "INSERT INTO drugs (drug_name, label_events) VALUES (?, ?)",
            (d["name"], json.dumps(d["label_events"])),
        )
        drug_id_by_name[d["name"]] = cur.lastrowid

    cur.executemany(
        "INSERT INTO ae_reports (drug_id, event_name, patient_age, patient_sex, report_date) VALUES (?, ?, ?, ?, ?)",
        [
            (
                drug_id_by_name[r["drug_name"]],
                r["event_name"],
                r["patient_age"],
                r["patient_sex"],
                r["report_date"],
            )
            for r in reports
        ],
    )
    conn.commit()
    conn.close()


def write_ground_truth(matrix: dict) -> None:
    signals = []
    for pair in ENGINEERED_PAIRS:
        signals.append(
            {
                "drug_name": pair["drug_name"],
                "event_name": pair["event_name"],
                "strength": pair["strength"],
                "is_trap_case": pair["is_trap_case"],
            }
        )
    # Also record a handful of deliberately-noted "noise" pairs (representative
    # spot-checks, not exhaustive -- most of the bulk volume is unlabeled noise).
    noise_spotchecks = [
        ("Renotrilax", "Weight Gain"),
        ("Dermaquil", "Vertigo"),
        ("Glucostabil", "Metallic Taste"),
        ("Osteofran", "Pruritus"),
    ]
    for drug_name, event_name in noise_spotchecks:
        signals.append(
            {
                "drug_name": drug_name,
                "event_name": event_name,
                "strength": "noise",
                "is_trap_case": False,
            }
        )

    with open(config.GROUND_TRUTH_PATH, "w") as f:
        json.dump({"signals": signals}, f, indent=2)


# ---------------------------------------------------------------------------
# Fictional literature corpus. Some entries corroborate the injected strong/
# borderline pairs, a couple contradict, the rest are irrelevant noise so a
# keyword search over this corpus is a genuine (if small) retrieval task.
# ---------------------------------------------------------------------------
def write_literature_corpus() -> None:
    publications = [
        # --- corroborates strong pair #1: Neuroclarin / Hepatic Enzyme Elevation ---
        {
            "id": "pub-001",
            "title": "Elevated Transaminases Observed in Neuroclarin Cohort: A Retrospective Case Series",
            "drug_name": "Neuroclarin",
            "event_name": "Hepatic Enzyme Elevation",
            "stance": "corroborates",
            "text": (
                "In a retrospective series of synthetic case reports, patients on Neuroclarin showed "
                "a marked and reproducible elevation in hepatic enzyme levels (ALT/AST) compared with "
                "matched controls, consistent with a hepatotoxic signal warranting monitoring."
            ),
        },
        {
            "id": "pub-002",
            "title": "Liver Function Panel Abnormalities Following Neuroclarin Initiation",
            "drug_name": "Neuroclarin",
            "event_name": "Hepatic Enzyme Elevation",
            "stance": "corroborates",
            "text": (
                "Serial liver function panels drawn after Neuroclarin initiation demonstrated a dose-independent "
                "rise in hepatic enzymes within the first eight weeks of therapy in a simulated patient registry."
            ),
        },
        {
            "id": "pub-003",
            "title": "Mechanistic Hypothesis for Neuroclarin-Associated Hepatic Enzyme Elevation",
            "drug_name": "Neuroclarin",
            "event_name": "Hepatic Enzyme Elevation",
            "stance": "corroborates",
            "text": (
                "We propose that Neuroclarin's primary metabolite accumulates in hepatocytes, plausibly "
                "explaining the pattern of hepatic enzyme elevation reported across multiple synthetic cohorts."
            ),
        },
        # --- corroborates strong pair #2: Vastocor / Tendon Rupture ---
        {
            "id": "pub-004",
            "title": "Achilles Tendon Rupture Cluster Among Vastocor Users",
            "drug_name": "Vastocor",
            "event_name": "Tendon Rupture",
            "stance": "corroborates",
            "text": (
                "A cluster of tendon rupture events, predominantly Achilles, was identified among a synthetic "
                "population of Vastocor users, with onset typically within three months of treatment start."
            ),
        },
        {
            "id": "pub-005",
            "title": "Tendinopathy Risk Signal in Vastocor Post-Marketing Surveillance Simulation",
            "drug_name": "Vastocor",
            "event_name": "Tendon Rupture",
            "stance": "corroborates",
            "text": (
                "Post-marketing surveillance simulation data flag a disproportionate reporting rate of tendon "
                "rupture in Vastocor-treated patients relative to the broader synthetic reporting background."
            ),
        },
        # --- corroborates borderline pair: Ferinox / Photosensitivity Rash ---
        {
            "id": "pub-006",
            "title": "Photosensitivity Reactions in a Small Ferinox Case Series",
            "drug_name": "Ferinox",
            "event_name": "Photosensitivity Rash",
            "stance": "corroborates",
            "text": (
                "A small case series describes photosensitivity rash in patients on Ferinox following sun "
                "exposure; the association is plausible but the case count remains modest and the signal "
                "borderline relative to background skin-reaction rates."
            ),
        },
        # --- contradicts entries (nuance) ---
        {
            "id": "pub-007",
            "title": "No Increased Photosensitivity Risk Detected in Larger Ferinox Registry Analysis",
            "drug_name": "Ferinox",
            "event_name": "Photosensitivity Rash",
            "stance": "contradicts",
            "text": (
                "A larger synthetic registry analysis of Ferinox users found no statistically meaningful "
                "increase in photosensitivity rash incidence once seasonal UV exposure was controlled for, "
                "contradicting the earlier small case series."
            ),
        },
        {
            "id": "pub-008",
            "title": "Reassessment of Tendon Rupture Reports in Vastocor: Confounding by Concomitant Fluoroquinolone Use",
            "drug_name": "Vastocor",
            "event_name": "Tendon Rupture",
            "stance": "contradicts",
            "text": (
                "Re-analysis suggests a substantial fraction of Vastocor tendon rupture reports involved "
                "concomitant fluoroquinolone exposure, a known independent risk factor, which may confound "
                "the apparent Vastocor association."
            ),
        },
        # --- trap case: Cardiozan / QT Interval Prolongation (already well-known/labeled) ---
        {
            "id": "pub-009",
            "title": "QT Interval Prolongation on Cardiozan: A Well-Characterized, Labeled Effect",
            "drug_name": "Cardiozan",
            "event_name": "QT Interval Prolongation",
            "stance": "corroborates",
            "text": (
                "QT interval prolongation on Cardiozan is a well-characterized, already-labeled effect dating "
                "to its original synthetic approval studies; continued reporting reflects known pharmacology "
                "rather than a novel finding."
            ),
        },
        # --- irrelevant / noise entries (unrelated drug/event combos or general topics) ---
        {
            "id": "pub-010",
            "title": "Dietary Sodium Intake and Cardiovascular Outcomes: A General Review",
            "drug_name": "",
            "event_name": "",
            "stance": "irrelevant",
            "text": (
                "This general review examines dietary sodium intake patterns and long-term cardiovascular "
                "outcomes across several synthetic population cohorts, unrelated to any specific drug therapy."
            ),
        },
        {
            "id": "pub-011",
            "title": "Pulmivex and Nasal Congestion: A Benign, Expected Finding",
            "drug_name": "Pulmivex",
            "event_name": "Nasal Congestion",
            "stance": "irrelevant",
            "text": (
                "Nasal congestion reports among Pulmivex users track the expected background rate for this "
                "drug class and do not represent a notable safety signal of any kind."
            ),
        },
        {
            "id": "pub-012",
            "title": "Renotrilax Pharmacokinetics in Renal Impairment: A Modeling Study",
            "drug_name": "Renotrilax",
            "event_name": "",
            "stance": "irrelevant",
            "text": (
                "This modeling study characterizes Renotrilax pharmacokinetics under varying degrees of "
                "synthetic renal impairment, with no adverse-event analysis component."
            ),
        },
        {
            "id": "pub-013",
            "title": "Dermaquil Manufacturing Process Improvements Reduce Batch Variability",
            "drug_name": "Dermaquil",
            "event_name": "",
            "stance": "irrelevant",
            "text": (
                "A process engineering report on Dermaquil manufacturing describes changes that reduced "
                "tablet batch-to-batch variability; no clinical safety data are discussed."
            ),
        },
        {
            "id": "pub-014",
            "title": "Glucostabil Weight Gain: Expected and Already Labeled",
            "drug_name": "Glucostabil",
            "event_name": "Weight Gain",
            "stance": "irrelevant",
            "text": (
                "Weight gain associated with Glucostabil is a long-established, already-labeled effect and "
                "is discussed here only in the context of patient counseling materials."
            ),
        },
        {
            "id": "pub-015",
            "title": "Hepacurin Formulation Comparison: Capsule vs. Tablet Bioavailability",
            "drug_name": "Hepacurin",
            "event_name": "",
            "stance": "irrelevant",
            "text": (
                "A bioequivalence study comparing Hepacurin capsule and tablet formulations found comparable "
                "bioavailability profiles; adverse events were not a study endpoint."
            ),
        },
        {
            "id": "pub-016",
            "title": "Osteofran Adherence Patterns in a Simulated Elderly Cohort",
            "drug_name": "Osteofran",
            "event_name": "",
            "stance": "irrelevant",
            "text": (
                "This adherence study of Osteofran in a simulated elderly cohort focuses on refill patterns "
                "and does not report on any adverse-event outcomes."
            ),
        },
        {
            "id": "pub-017",
            "title": "General Principles of Disproportionality Analysis in Pharmacovigilance",
            "drug_name": "",
            "event_name": "",
            "stance": "irrelevant",
            "text": (
                "This methodological overview discusses proportional reporting ratios and other "
                "disproportionality metrics in general terms, without reference to any specific synthetic "
                "drug or event."
            ),
        },
        {
            "id": "pub-018",
            "title": "Insomnia Prevalence in the General Synthetic Population",
            "drug_name": "",
            "event_name": "Insomnia",
            "stance": "irrelevant",
            "text": (
                "Background insomnia prevalence in the general synthetic population is discussed here as "
                "context for interpreting adverse-event reporting rates across unrelated drug classes."
            ),
        },
        {
            "id": "pub-019",
            "title": "Cognitive Fog Complaints Across Multiple Drug Classes: A Scoping Review",
            "drug_name": "",
            "event_name": "Cognitive Fog",
            "stance": "irrelevant",
            "text": (
                "This scoping review surveys cognitive fog complaints across many unrelated synthetic drug "
                "classes and concludes the symptom is nonspecific and poorly discriminating."
            ),
        },
        {
            "id": "pub-020",
            "title": "Hyperkalemia Monitoring Best Practices in Renotrilax-Treated Patients",
            "drug_name": "Renotrilax",
            "event_name": "Hyperkalemia",
            "stance": "irrelevant",
            "text": (
                "Routine potassium monitoring guidance for Renotrilax-treated patients is summarized here; "
                "hyperkalemia in this population tracks the drug's already-labeled expected profile."
            ),
        },
        {
            "id": "pub-021",
            "title": "Xerostomia Self-Reporting Bias in Longitudinal Drug Safety Studies",
            "drug_name": "",
            "event_name": "Xerostomia",
            "stance": "irrelevant",
            "text": (
                "Self-reporting bias in dry-mouth (xerostomia) complaints is examined across several "
                "longitudinal synthetic drug safety studies, independent of any single product."
            ),
        },
        {
            "id": "pub-022",
            "title": "Metallic Taste as a Transient, Benign Symptom Across Drug Classes",
            "drug_name": "",
            "event_name": "Metallic Taste",
            "stance": "irrelevant",
            "text": (
                "Metallic taste complaints are generally transient and benign across many unrelated synthetic "
                "drug classes, per this cross-sectional survey."
            ),
        },
        {
            "id": "pub-023",
            "title": "Pruritus Reporting Rates in Dermatologic Drug Trials: A Meta-Analysis",
            "drug_name": "Dermaquil",
            "event_name": "Pruritus",
            "stance": "irrelevant",
            "text": (
                "This meta-analysis of dermatologic drug trials, including Dermaquil, finds pruritus reporting "
                "rates consistent with the already-labeled background expectation for the drug class."
            ),
        },
        {
            "id": "pub-024",
            "title": "Orthostatic Hypotension Screening Protocols in Elderly Patients on Osteofran",
            "drug_name": "Osteofran",
            "event_name": "Orthostatic Hypotension",
            "stance": "irrelevant",
            "text": (
                "Screening protocols for orthostatic hypotension in elderly Osteofran patients are reviewed; "
                "the effect is already labeled and well understood."
            ),
        },
        {
            "id": "pub-025",
            "title": "Peripheral Neuropathy: A Cross-Drug-Class Literature Survey",
            "drug_name": "",
            "event_name": "Peripheral Neuropathy",
            "stance": "irrelevant",
            "text": (
                "This survey compiles peripheral neuropathy reporting across many unrelated synthetic drug "
                "classes without identifying any single disproportionate association."
            ),
        },
        {
            "id": "pub-026",
            "title": "Hypoglycemia Risk Factors Unrelated to Pharmacotherapy",
            "drug_name": "",
            "event_name": "Hypoglycemia",
            "stance": "irrelevant",
            "text": (
                "Non-pharmacologic risk factors for hypoglycemia, such as fasting behavior and physical "
                "exertion, are the focus of this general clinical review."
            ),
        },
    ]

    with open(config.LITERATURE_CORPUS_PATH, "w") as f:
        json.dump({"publications": publications}, f, indent=2)


def main() -> None:
    matrix = build_final_matrix()
    reports = generate_reports(matrix)
    write_db(reports)
    write_ground_truth(matrix)
    write_literature_corpus()

    print(f"Generated {len(reports)} ae_reports rows across {len(DRUGS)} drugs and {len(EVENTS)} events.")
    print(f"Wrote {config.DB_PATH}")
    print(f"Wrote {config.GROUND_TRUTH_PATH}")
    print(f"Wrote {config.LITERATURE_CORPUS_PATH}")
    print()
    print("Engineered pair PRR values (computed from final matrix):")
    for pair in ENGINEERED_PAIRS:
        result = compute_prr(matrix, pair["drug_name"], pair["event_name"])
        print(
            f"  [{pair['strength']:>10}]{' (TRAP)' if pair['is_trap_case'] else '       '} "
            f"{pair['drug_name']:>12} / {pair['event_name']:<28} "
            f"case_count={result['case_count']:>5}  PRR={result['prr']:.3f}"
        )


if __name__ == "__main__":
    main()
