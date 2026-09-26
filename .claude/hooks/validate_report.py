#!/usr/bin/env python3
"""
PostToolUse dev/ops hook: validates reviewer-ready reports written under
reports/*.md by a Write or Edit tool call.

This is a DEV/OPS guardrail for the Claude Code CLI session building this
repo, distinct from the runtime pipeline's own agents/hooks.py guardrail
(which gates the LangGraph pipeline's own tool calls, e.g. record_signal_decision).
This hook instead catches a *developer* (human or Claude Code) accidentally
writing/editing a demo report file that is missing required safety markers.

Contract (Claude Code PostToolUse hook):
  - Reads a single JSON object from stdin with at least:
      session_id, cwd, hook_event_name, tool_name, tool_input, tool_response
  - PostToolUse fires AFTER the tool already ran, so it cannot truly "block"
    the write — it can only report a problem back. Per the hook exit-code
    protocol, exit code 2 is the closest available "blocking" signal (stderr
    is surfaced to the model as an error) for a real validation failure;
    exit 0 means no problem (either the file doesn't match reports/*.md, or
    it matched and passed every check).
  - Must not crash on unexpected/malformed input (falls back to a no-op,
    exit 0) but must NOT silently pass a report that genuinely fails its
    checks -- that is a real validation failure, not a fail-open case.
"""
import fnmatch
import json
import sys
from pathlib import Path

BANNER_TEXT = "SYNTHETIC DEMO — NOT REAL DATA"
APPROVAL_MARKERS = ("review status", "approval")
BAD_PHRASES = ("causes", "proven to cause", "confirmed to cause")

TARGET_GLOB = "reports/*.md"


def project_root() -> Path:
    # .claude/hooks/validate_report.py -> .claude/hooks -> .claude -> <project root>
    return Path(__file__).resolve().parent.parent.parent


def extract_file_path(payload: dict):
    tool_input = payload.get("tool_input") or {}
    # Write/Edit tools both use "file_path" for the target file.
    return tool_input.get("file_path")


def relative_target(file_path: str, root: Path):
    try:
        p = Path(file_path)
        if not p.is_absolute():
            p = (root / p).resolve()
        else:
            p = p.resolve()
        rel = p.relative_to(root)
        return p, rel
    except Exception:
        return None, None


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        # Can't parse the hook payload at all -- nothing sensible to validate.
        return 0

    try:
        file_path = extract_file_path(payload)
        if not file_path:
            return 0

        root = project_root()
        abs_path, rel_path = relative_target(file_path, root)
        if abs_path is None:
            return 0

        # Only care about reports/*.md (top-level of reports/, matching the
        # glob literally -- not report subdirectories).
        if not fnmatch.fnmatch(rel_path.as_posix(), TARGET_GLOB):
            return 0

        if not abs_path.exists():
            # PostToolUse should fire after the write, but if the file is
            # somehow gone, there's nothing on disk to validate.
            print(
                f"validate_report: target {rel_path} does not exist on disk; skipping.",
                file=sys.stderr,
            )
            return 0

        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"validate_report: could not read {rel_path}: {e}", file=sys.stderr)
            return 0

        failures = []

        if BANNER_TEXT not in content:
            failures.append(
                f"missing required synthetic-data banner text: {BANNER_TEXT!r}"
            )

        lowered = content.lower()

        if not any(marker in lowered for marker in APPROVAL_MARKERS):
            failures.append(
                "missing a human-approval / review-status marker "
                f"(expected one of: {', '.join(APPROVAL_MARKERS)})"
            )

        found_bad = [phrase for phrase in BAD_PHRASES if phrase in lowered]
        if found_bad:
            failures.append(
                "contains unhedged causal language: " + ", ".join(found_bad)
            )

        if failures:
            print(f"validate_report: {rel_path} FAILED validation:", file=sys.stderr)
            for f in failures:
                print(f"  - {f}", file=sys.stderr)
            return 2

        return 0
    except Exception as e:
        # Unexpected internal error in the hook itself -- don't crash the
        # session over a hook bug, but make sure it's visible.
        print(f"validate_report: internal hook error: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
