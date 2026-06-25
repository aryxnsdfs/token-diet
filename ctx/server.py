"""MCP server (§4.1) — what makes `/` light up.

`ctx serve` starts a FastMCP server over stdio; the host launches it as a
subprocess. From the one command registry it exposes:

- **Prompts** (`@mcp.prompt`) — user-invoked, rendered in the host's `/` menu.
- **Tools** (`@mcp.tool`) — model-invoked, callable mid-task.
- **Resources** — the repo map, attachable by the host.

Same handlers, two entry points — both generated from `registry.py`.
"""

from __future__ import annotations

from .commands import ParsedCommand, dispatch
from .config import Project
from .engine.facade import Engine
from .registry import Cmd, enabled_registry


def build_server(engine: Engine | None = None):
    """Construct (but don't run) the FastMCP server. Raises if MCP is absent."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "MCP SDK not installed. `pipx install 'ctx-engine[mcp]'` or "
            "`pip install mcp`."
        ) from exc

    engine = engine or Engine()
    mcp = FastMCP("ctx")
    reg = enabled_registry(engine)

    def make_handler(cmd: Cmd):
        def run(argument: str = "") -> str:
            parsed = ParsedCommand(cmd.name, [argument] if argument else [], cmd.usage())
            return dispatch(engine, parsed, text=argument).text
        run.__name__ = f"cmd_{cmd.name}"
        run.__doc__ = f"{cmd.desc}. Usage: {cmd.usage()}"
        return run

    for name, cmd in reg.items():
        handler = make_handler(cmd)
        if cmd.as_prompt:
            mcp.prompt(name=name, description=cmd.desc)(handler)
        if cmd.as_tool:
            mcp.tool(name=name, description=f"{cmd.desc}. Args: {cmd.args or 'none'}")(handler)

    @mcp.resource("ctx://repo-map")
    def repo_map() -> str:
        """The current graph-ranked repo map."""
        return engine.map().text

    @mcp.resource("ctx://decisions")
    def decisions() -> str:
        """The append-only decision log."""
        return engine.memory.render_log()

    return mcp


def serve(project: Project | None = None, *, http: bool = False,
          host: str = "127.0.0.1", port: int = 8000) -> None:
    engine = Engine(project)
    mcp = build_server(engine)
    if not http:
        mcp.run(transport="stdio")
        return
    # HTTP/SSE transport for hosted connectors (Cowork / Claude.ai).
    try:
        mcp.settings.host = host
        mcp.settings.port = port
    except Exception:
        pass
    for transport in ("streamable-http", "sse"):
        try:
            mcp.run(transport=transport)
            return
        except (TypeError, ValueError):
            continue
    raise RuntimeError("installed MCP SDK has no HTTP transport; upgrade `mcp`")
