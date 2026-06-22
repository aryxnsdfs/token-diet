"""Per-host registration adapters (§4.1, §5.3).

`ctx init` detects the host and writes whatever wiring that host needs so
pressing `/` lights up the commands. Each adapter is independent; `ctx doctor`
verifies the result.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Project
from ..engine.facade import Engine
from .claude_code import write_claude_code
from .cowork import write_cowork
from .generic import write_generic


def detect_host(root: Path) -> str:
    """Best-effort host detection from on-disk markers."""
    if (root / ".claude").exists() or (root / ".mcp.json").exists():
        return "claude_code"
    if (root / ".cowork").exists():
        return "cowork"
    return "generic"


def register_all(project: Project, engine: Engine) -> dict[str, list[str]]:
    """Write registration for every adapter; return files written per host."""
    written: dict[str, list[str]] = {}
    written["claude_code"] = write_claude_code(project, engine)
    written["cowork"] = write_cowork(project, engine)
    written["generic"] = write_generic(project, engine)
    return written
