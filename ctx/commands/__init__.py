"""Command handlers.

The handler *logic* lives on `ctx.engine.facade.Engine` (one method per
command) so the MCP server, the proxy, and the CLI share one implementation.
This package adds the surface glue: parsing `/text` typed by a user on a host
that can't render MCP prompts (Mode B), and dispatching to the registry.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from ..engine.facade import CommandResult, Engine
from ..registry import Cmd, enabled_registry


@dataclass
class ParsedCommand:
    name: str
    args: list[str]
    raw: str


def parse_slash(text: str) -> ParsedCommand | None:
    """Parse a leading `/name arg1 arg2` out of *text*. None if not a command."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    body = text[1:]
    if not body:
        return None
    try:
        parts = shlex.split(body)
    except ValueError:
        parts = body.split()
    if not parts:
        return None
    return ParsedCommand(name=parts[0], args=parts[1:], raw=text)


# Maps positional CLI/proxy args -> the kwarg each handler expects.
_ARG0 = {"overview": "path", "show": "target", "lookup": "symbol", "model": "tier"}


def dispatch(engine: Engine, parsed: ParsedCommand, *, history=None,
             text: str = "") -> CommandResult:
    reg = enabled_registry(engine)
    cmd = reg.get(parsed.name)
    if cmd is None:
        avail = ", ".join(f"/{k}" for k in reg)
        return CommandResult(f"Unknown command /{parsed.name}. Available: {avail}", {})
    kwargs: dict = {}
    if parsed.name in _ARG0 and parsed.args:
        kwargs[_ARG0[parsed.name]] = parsed.args[0]
    if parsed.name == "cleanup":
        kwargs["history"] = history or []
    if parsed.name == "patch":
        kwargs["text"] = text
    return cmd.handler(**kwargs)
