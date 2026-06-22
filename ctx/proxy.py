"""Mode B — Local proxy (§4.2).

A `localhost` server the client points at instead of the provider endpoint. It
optimizes every request without the host needing MCP support, then forwards the
shaped payload upstream. Also parses `/text` commands the user types, since
non-MCP hosts can't render the `/` menu (§4.3).

Execution flow: receive → convert attachments → evaluate/compress history →
assemble layered prompt → measure → dispatch upstream → log + return.
"""

from __future__ import annotations

import os
from pathlib import Path

from .commands import dispatch, parse_slash
from .config import Project
from .engine.assembler import Assembler
from .engine.budget import TextItem
from .engine.facade import Engine
from .engine.memory import Turn

PROVIDER_URLS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/chat/completions",
}


def _extract_turns(messages: list[dict]) -> list[Turn]:
    out: list[Turn] = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):  # provider block format
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        out.append(Turn(role=m.get("role", "user"), content=str(content)))
    return out


def build_app(project: Project | None = None):
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Proxy deps missing. `pipx install 'ctx-engine[proxy]'`."
        ) from exc

    engine = Engine(project)
    app = FastAPI(title="ctx proxy", version="0.1.0")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "root": str(engine.project.root)}

    @app.post("/v1/messages")
    @app.post("/v1/chat/completions")
    async def proxy(request: Request):
        provider = "openai" if "chat/completions" in str(request.url) else "anthropic"
        payload = await request.json()
        messages = payload.get("messages", [])
        turns = _extract_turns(messages)

        # Intercept a trailing /command the user typed (Mode B, no autocomplete).
        last = turns[-1].content if turns else ""
        parsed = parse_slash(last)
        if parsed is not None:
            result = dispatch(engine, parsed, history=turns[:-1], text=last)
            return JSONResponse({
                "ctx_command": parsed.name,
                "content": result.text,
                "meta": result.meta,
            })

        # Compress old history into the decision log when it grows long.
        comp = engine.memory.compress(turns)
        recent = comp.get("recent", turns)

        # Assemble the layered, cache-friendly prompt.
        asm: Assembler = engine.assembler
        snippets = [TextItem(rungs=[t.content], relevance=1.0, priority=1)
                    for t in recent if t.role == "user"]
        assembled = asm.assemble(
            system=_system_of(payload),
            repo_map=engine.map().text,
            decision_log=engine.memory.render_log(),
            verbatim=recent,
            user_message=last,
        )

        # Measure → record telemetry → dispatch upstream.
        engine.telemetry.record(
            model=payload.get("model", engine.model),
            fresh_in=assembled.fresh_tokens,
            cached_in=assembled.cached_tokens,
            out=0,
            command="proxy",
        )

        api_key = _provider_key(provider, request)
        if not api_key:
            return JSONResponse(
                {"error": "no upstream API key; set ANTHROPIC_API_KEY / OPENAI_API_KEY",
                 "assembled_tokens": assembled.total_tokens,
                 "cached_tokens": assembled.cached_tokens}, status_code=400)

        shaped = _reshape(payload, assembled, provider)
        async with httpx.AsyncClient(timeout=120) as client:
            upstream = await client.post(
                PROVIDER_URLS[provider], json=shaped,
                headers=_headers(provider, api_key),
            )
        return JSONResponse(upstream.json(), status_code=upstream.status_code)

    return app


def _system_of(payload: dict) -> str:
    sysv = payload.get("system", "")
    if isinstance(sysv, list):
        return "\n".join(p.get("text", "") for p in sysv if isinstance(p, dict))
    return str(sysv)


def _provider_key(provider: str, request) -> str:
    if provider == "anthropic":
        return request.headers.get("x-api-key") or os.environ.get("ANTHROPIC_API_KEY", "")
    return (request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            or os.environ.get("OPENAI_API_KEY", ""))


def _headers(provider: str, key: str) -> dict:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"}
    return {"authorization": f"Bearer {key}", "content-type": "application/json"}


def _reshape(payload: dict, assembled, provider: str) -> dict:
    """Replace messages with the assembled, cache-broken prompt."""
    out = dict(payload)
    msgs = assembled.to_messages()
    if provider == "anthropic":
        sys_msg = next((m for m in msgs if m["role"] == "system"), None)
        out["system"] = sys_msg["content"] if sys_msg else ""
        out["messages"] = [m for m in msgs if m["role"] != "system"]
    else:
        out["messages"] = msgs
    return out


def run(host: str = "127.0.0.1", port: int = 8000, project: Project | None = None) -> None:
    import uvicorn

    uvicorn.run(build_app(project), host=host, port=port)
