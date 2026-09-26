#!/usr/bin/env python3
"""
PreToolUse dev/ops hook: logs every Claude Code tool call to a JSONL file.

This is a DEV/OPS logging hook for the Claude Code CLI session (interactive
dev work or the .claude/skills layer) working on this repo. It is distinct
from the runtime pipeline's own observability log (logs/observability.jsonl,
written by the LangGraph pipeline itself) — this file logs Claude Code tool
calls made while *building/maintaining* the repo, not pipeline agent activity.

Contract (Claude Code PreToolUse hook):
  - Reads a single JSON object from stdin with at least:
      session_id, cwd, hook_event_name, tool_name, tool_input, tool_use_id
  - Must fail open: any error (bad JSON, missing fields, I/O problem) must
    still result in exit code 0, so a bug in this script never blocks the
    development session. It never prints a "deny" decision.
  - On success, appends one JSON line to logs/cc_dev_tool_use.jsonl and
    exits 0 with no stdout (nothing to report to the model).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_SUMMARY_CHARS = 500


def project_root() -> Path:
    # .claude/hooks/log_tool_use.py -> .claude/hooks -> .claude -> <project root>
    return Path(__file__).resolve().parent.parent.parent


def summarize(value) -> str:
    try:
        text = json.dumps(value, default=str)
    except Exception:
        text = str(value)
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS] + "...<truncated>"
    return text


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_name": payload.get("tool_name"),
            "tool_input_summary": summarize(payload.get("tool_input")),
            "cwd": payload.get("cwd"),
            "session_id": payload.get("session_id"),
            "hook_event_name": payload.get("hook_event_name"),
        }

        root = project_root()
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "cc_dev_tool_use.jsonl"

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        # Fail open no matter what goes wrong here.
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
