"""Claude Code adapter (§4.1).

Writes a project `.mcp.json` so the host launches `ctx serve`, plus one
`.claude/commands/*.md` per command as a native belt-and-suspenders path.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import Project
from ..engine.facade import Engine
from ..registry import enabled_registry

_MCP_JSON = {
    "mcpServers": {
        "ctx": {"command": "ctx", "args": ["serve"]}
    }
}


def _command_md(name: str, desc: str, args: str) -> str:
    has_arg = bool(args.strip())
    body = "$ARGUMENTS" if has_arg else ""
    return (
        "---\n"
        f"description: {desc}\n"
        + ("argument-hint: " + args + "\n" if has_arg else "")
        + "---\n\n"
        f"Run the ctx `{name}` command via the MCP server (tool `ctx:{name}`)"
        + (f" with argument: {body}" if has_arg else "")
        + ".\n\n"
        f"This invokes the engine handler for `/{name}` and injects the result "
        "into context.\n"
    )


def write_claude_code(project: Project, engine: Engine) -> list[str]:
    written: list[str] = []
    root = project.root

    mcp_path = root / ".mcp.json"
    existing = {}
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.setdefault("mcpServers", {})["ctx"] = _MCP_JSON["mcpServers"]["ctx"]
    mcp_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    written.append(str(mcp_path))

    cmd_dir = root / ".claude" / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    for name, cmd in enabled_registry(engine).items():
        if not cmd.as_prompt:
            continue
        md = cmd_dir / f"{name}.md"
        md.write_text(_command_md(name, cmd.desc, cmd.args), encoding="utf-8")
        written.append(str(md))
    return written
