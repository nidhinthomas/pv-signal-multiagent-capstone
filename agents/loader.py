"""Parses .claude/agents/*.md subagent spec files into LangGraph node config.

This is what makes the Claude Code "Sub-agents" primitive genuinely
load-bearing under a LangGraph runtime: each file's YAML frontmatter and
system-prompt body are read here and used directly to configure a graph
node's tool allowlist and system message, rather than being decorative
files that only Claude Code's own CLI dispatch would ever read.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

AGENTS_DIR = config.ROOT_DIR / ".claude" / "agents"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    tools: list[str]
    system_prompt: str
    source_path: Path


def _parse_tools(raw) -> list[str]:
    """Frontmatter `tools:` is either a YAML list (`[]`) or a comma-separated
    string (`scan_signals, calculate_prr, get_signal_history`)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    raise ValueError(f"Unrecognized `tools:` value: {raw!r}")


def parse_agent_file(path: Path) -> AgentSpec:
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing YAML frontmatter (must start with '---')")

    _, frontmatter_raw, body = text.split("---", 2)
    frontmatter = yaml.safe_load(frontmatter_raw) or {}

    for required_key in ("name", "description"):
        if required_key not in frontmatter:
            raise ValueError(f"{path}: frontmatter missing required key '{required_key}'")

    return AgentSpec(
        name=frontmatter["name"],
        description=frontmatter["description"],
        tools=_parse_tools(frontmatter.get("tools")),
        system_prompt=body.strip(),
        source_path=path,
    )


def load_all_agents(agents_dir: Path = AGENTS_DIR) -> dict[str, AgentSpec]:
    specs: dict[str, AgentSpec] = {}
    for path in sorted(agents_dir.glob("*.md")):
        spec = parse_agent_file(path)
        specs[spec.name] = spec
    return specs


if __name__ == "__main__":
    for name, spec in load_all_agents().items():
        print(f"{name}: tools={spec.tools} prompt_chars={len(spec.system_prompt)}")
