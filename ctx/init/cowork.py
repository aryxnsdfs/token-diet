"""Claude Cowork / Claude.ai adapter (§4.1).

Emits the connector config the app needs: a stdio command + args locally, or an
HTTP/SSE URL if the server is hosted. Written to `.ctx/cowork.connector.json`
for the user to paste into the app's connector settings.
"""

from __future__ import annotations

import json

from ..config import Project
from ..engine.facade import Engine


def write_cowork(project: Project, engine: Engine) -> list[str]:
    connector = {
        "name": "ctx",
        "description": "Local token-saving context engine",
        "transport": {
            "type": "stdio",
            "command": "ctx",
            "args": ["serve"],
        },
        "hosted_alternative": {
            "type": "http",
            "url": "http://127.0.0.1:8000/mcp",
            "note": "Use when running `ctx serve --http` on a reachable host.",
        },
    }
    out = project.ctx_dir / "cowork.connector.json"
    project.ensure_dirs()
    out.write_text(json.dumps(connector, indent=2) + "\n", encoding="utf-8")
    return [str(out)]
