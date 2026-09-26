"""Shared constants for the pharmacovigilance signal-detection capstone.

Single source of truth for thresholds, paths, and model wiring so agents,
the MCP server, the graph, and the UI never hardcode these independently.
"""

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

# --- Data & storage paths ---
DB_PATH = ROOT_DIR / "data" / "db.sqlite"
GROUND_TRUTH_PATH = ROOT_DIR / "data" / "ground_truth_signals.json"
LITERATURE_CORPUS_PATH = ROOT_DIR / "data" / "literature_corpus.json"
SIGNAL_HISTORY_DB_PATH = ROOT_DIR / "memory" / "signal_history.sqlite"
CHECKPOINT_DB_PATH = ROOT_DIR / "runs" / "checkpoints.sqlite"
RUNS_DIR = ROOT_DIR / "runs"
REPORTS_DIR = ROOT_DIR / "reports"
LOGS_DIR = ROOT_DIR / "logs"
OBSERVABILITY_LOG_PATH = LOGS_DIR / "observability.jsonl"

# --- Signal detection thresholds (disproportionality analysis) ---
# PRR (Proportional Reporting Ratio) threshold above which a drug-event pair
# is flagged as a candidate signal, subject to a minimum case count so a
# single rare report can't trigger a flag on its own.
PRR_THRESHOLD = 2.0
MIN_CASE_COUNT = 3

# --- LLM wiring (via OpenRouter) ---
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
# Exact slug confirmed at build time (Phase 0 smoke test) against
# OpenRouter's /models endpoint -- do not guess, verify.
OPENROUTER_MODEL_SLUG = os.environ.get("PV_MODEL_SLUG", "anthropic/claude-sonnet-5")

# --- Governance / disclaimers ---
SYNTHETIC_DATA_BANNER = "SYNTHETIC DEMO — NOT REAL DATA"

# --- Guardrail allowlists ---
# Only these paths may be read by MCP tools that touch the filesystem.
ALLOWED_DATA_PATHS = {DB_PATH, LITERATURE_CORPUS_PATH, GROUND_TRUTH_PATH}
