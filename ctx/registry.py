"""The command registry — single source of truth (§5.1).

One declarative table. The MCP server, the Claude Code `.md` files, the proxy
parser, and any CLI autocomplete are all generated from it. A command is
exposed as an MCP *Prompt* (user types `/name`) and/or an MCP *Tool* (the model
calls it mid-task) — see `as_prompt` / `as_tool`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .engine.facade import CommandResult, Engine
from .engine.memory import Turn


@dataclass
class Cmd:
    name: str
    desc: str
    args: str
    handler: Callable[..., CommandResult]
    as_prompt: bool = True   # user-invoked via /name
    as_tool: bool = True     # model-invoked

    def usage(self) -> str:
        return f"/{self.name} {self.args}".strip()


def build_registry(engine: Engine) -> dict[str, Cmd]:
    """Bind handlers to a live engine. Mirrors §5.1's COMMANDS table."""
    return {
        "init": Cmd("init", "Warm the chat: build index, inject map, enable diff mode",
                    "", lambda **kw: engine.start(), as_tool=False),
        "map": Cmd("map", "Inject the graph-ranked repo map", "[path]",
                   lambda path="", **kw: engine.map(path)),
        "focus": Cmd("focus", "Pin a file or symbol into context", "<file|symbol>",
                     lambda target="", **kw: engine.focus(target)),
        "explain": Cmd("explain", "Pull just one symbol's body", "<symbol>",
                       lambda symbol="", **kw: engine.explain(symbol)),
        "diff": Cmd("diff", "Force diff-only output mode", "",
                    lambda **kw: engine.diff_mode()),
        "compress": Cmd("compress", "Distill history to the decision log now", "",
                        lambda history=None, **kw: engine.compress(history or [])),
        "cost": Cmd("cost", "Show token + cache telemetry", "",
                    lambda **kw: engine.cost()),
        "route": Cmd("route", "Force a model tier", "<tier>",
                     lambda tier="", **kw: engine.route(tier)),
        "apply": Cmd("apply", "Apply SEARCH/REPLACE edits from the last reply", "",
                     lambda text="", dry_run=False, **kw: engine.apply_model_output(
                         text, dry_run=dry_run)),
    }


def enabled_registry(engine: Engine) -> dict[str, Cmd]:
    """Apply the project's `enabled_commands` filter (empty = all)."""
    reg = build_registry(engine)
    allow = set(engine.config.enabled_commands)
    if not allow:
        return reg
    return {k: v for k, v in reg.items() if k in allow}
