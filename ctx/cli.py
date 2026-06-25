"""`ctx` command-line entry point.

Subcommands: init · index · serve · doctor · proxy · map · cost.
`ctx init` runs once per project: builds the index, registers with the detected
host, writes host-native command files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config, Project, find_project_root


def _engine(root: Path | None = None):
    from .engine.facade import Engine
    return Engine(Project(root))


def cmd_init(args: argparse.Namespace) -> int:
    from .init import detect_host, register_all

    root = find_project_root(Path(args.path) if args.path else None)
    project = Project(root)
    project.ensure_dirs()
    if not project.config_path.exists():
        project.write_config(Config())

    print(f"ctx init in {root}")
    engine = _engine(root)

    print("  building code index …")
    counts = engine.build_index()
    print(f"    {counts['symbols']} symbols, {counts['parsed']} parsed, "
          f"{counts['skipped']} cached")

    host = detect_host(root)
    print(f"  detected host: {host}")
    written = register_all(project, engine)
    for h, files in written.items():
        for f in files:
            print(f"    [{h}] wrote {Path(f).name}")

    print("\nDone. In Claude Code, press / and pick a ctx command "
          "(or run `ctx doctor` to verify wiring).")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    engine = _engine()
    counts = engine.build_index()
    print(f"Indexed: {counts}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve
    try:
        serve(http=args.http, host=args.host, port=args.port)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_proxy(args: argparse.Namespace) -> int:
    from .proxy import run
    try:
        run(host=args.host, port=args.port)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .engine.budget import using_real_tokenizer

    root = find_project_root()
    project = Project(root)
    print(f"ctx doctor — project: {root}\n")

    def check(label: str, ok: bool, hint: str = "") -> None:
        mark = "ok " if ok else "MISS"
        print(f"  [{mark}] {label}" + (f"  ({hint})" if not ok and hint else ""))

    check(".ctx initialized", project.initialized(), "run `ctx init`")
    check("index.db present", project.index_db.exists(), "run `ctx index`")
    check(".mcp.json present", (root / ".mcp.json").exists(), "run `ctx init`")
    check("config.toml present", project.config_path.exists())

    # optional deps
    def have(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except ImportError:
            return False

    print("\n  optional deps (graceful fallbacks apply when missing):")
    check("mcp (FastMCP server)", have("mcp"), "pip install mcp")
    check("fastapi/uvicorn/httpx (proxy)", have("fastapi"), "pip install fastapi uvicorn httpx")
    check("tree_sitter_language_pack (rich parse)", have("tree_sitter_language_pack"),
          "pip install tree-sitter-language-pack")
    check("tiktoken (real tokenizer)", using_real_tokenizer(),
          "pip install tiktoken — currently using char estimate")
    check("markitdown (doc pipeline)", have("markitdown"), "pip install markitdown")
    check("networkx (graph rank)", have("networkx"), "pip install networkx")
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    engine = _engine()
    if not engine.index.symbol_count():
        engine.build_index()
    print(engine.map(getattr(args, "path", "") or "").text)
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    print(_engine().cost().text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctx", description="token-saving context engine")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("init", help="set up ctx in a project (run once)")
    pi.add_argument("path", nargs="?", help="project root (default: cwd)")
    pi.set_defaults(func=cmd_init)

    px = sub.add_parser("index", help="(re)build the code index")
    px.set_defaults(func=cmd_index)

    ps = sub.add_parser("serve", help="run the MCP server (stdio, or --http)")
    ps.add_argument("--http", action="store_true", help="serve over HTTP/SSE instead of stdio")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8000)
    ps.set_defaults(func=cmd_serve)

    pp = sub.add_parser("proxy", help="run the Mode B local proxy")
    pp.add_argument("--host", default="127.0.0.1")
    pp.add_argument("--port", type=int, default=8000)
    pp.set_defaults(func=cmd_proxy)

    pd = sub.add_parser("doctor", help="verify wiring and optional deps")
    pd.set_defaults(func=cmd_doctor)

    pm = sub.add_parser("showrepo", help="show the whole project structure")
    pm.add_argument("path", nargs="?")
    pm.set_defaults(func=cmd_map)
    for alias in ("overview", "map"):
        sub.add_parser(alias, help=f"(alias for showrepo)").set_defaults(func=cmd_map)

    pc = sub.add_parser("showstats", help="show token and cost savings")
    pc.set_defaults(func=cmd_cost)
    for alias in ("savings", "cost"):
        sub.add_parser(alias, help=f"(alias for showstats)").set_defaults(func=cmd_cost)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
