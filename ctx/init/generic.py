"""Generic / non-MCP adapter (§4.1, §4.3).

For MCP hosts that only need the stdio launch command, and for non-MCP tools
that fall back to the Mode B proxy. Writes a short instructions file plus a
proxy launch hint.
"""

from __future__ import annotations

import json

from ..config import Project
from ..engine.facade import Engine


def write_generic(project: Project, engine: Engine) -> list[str]:
    project.ensure_dirs()
    info = {
        "mcp_stdio": {"command": "ctx", "args": ["serve"]},
        "proxy": {
            "start": "ctx proxy --port 8000",
            "point_client_base_url_to": "http://127.0.0.1:8000",
            "note": "Type /overview, /show, etc. as the message; the proxy parses it.",
        },
    }
    out = project.ctx_dir / "generic.setup.json"
    out.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return [str(out)]
