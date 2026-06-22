"""Engine facade — one object the front-ends drive.

Wires the index, repo map, memory, docs, patch, router, assembler and
telemetry into the handful of operations the slash commands and proxy need.
Every command handler ultimately calls a method here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Project
from .assembler import Assembler
from .budget import TextItem, count_tokens
from .docs import DocPipeline
from .index import CodeIndex, iter_source_files
from .memory import MemoryStore, Turn
from . import patch as patchmod
from .repomap import render_map
from .router import Router, Task
from .telemetry import Telemetry


@dataclass
class CommandResult:
    """What a handler returns: text injected into chat + structured meta."""

    text: str
    meta: dict


class Engine:
    def __init__(self, project: Project | None = None) -> None:
        self.project = project or Project()
        self.project.ensure_dirs()
        self.config = self.project.load_config()
        self.model = self.config.tokenizer_model
        self._index: CodeIndex | None = None
        self.memory = MemoryStore(
            self.project.decisions,
            local_model=self.config.models.local,
        )
        self.docs = DocPipeline(self.project.cache_dir)
        self.router = Router(self.config.models)
        self.assembler = Assembler(self.config.budgets, self.model)
        self.telemetry = Telemetry(self.project.cache_dir / "telemetry.jsonl")
        self.diff_mode_on = False

    # -- index lifecycle ----------------------------------------------------

    @property
    def index(self) -> CodeIndex:
        if self._index is None:
            self._index = CodeIndex(self.project.index_db)
        return self._index

    def build_index(self) -> dict:
        return self.index.index_tree(self.project.root)

    # -- commands -----------------------------------------------------------

    def map(self, path: str = "", *, mentioned: list[str] | None = None,
            recent: list[str] | None = None) -> CommandResult:
        budget = int((self.config.budgets.window - self.config.budgets.generation_reserve)
                     * self.config.budgets.repo_map)
        focus = [str((self.project.root / path).resolve())] if path else []
        text = render_map(
            self.index, token_budget=budget, model=self.model,
            focus_files=focus, mentioned=mentioned or [], recent_files=recent or [],
        )
        return CommandResult(text, {"tokens": count_tokens(text, self.model),
                                    "budget": budget})

    def focus(self, target: str) -> CommandResult:
        """Pin a file or symbol into context (full detail)."""
        p = (self.project.root / target).resolve()
        if p.exists() and p.is_file():
            body = p.read_text(encoding="utf-8", errors="replace")
            text = f"# Focused file: {target}\n```\n{body}\n```"
            return CommandResult(text, {"kind": "file", "tokens": count_tokens(text, self.model)})
        syms = self.index.find_symbol(target)
        if not syms:
            return CommandResult(f"(no file or symbol named `{target}`)", {"kind": "miss"})
        return self.explain(target)

    def explain(self, symbol: str) -> CommandResult:
        """Pull just one symbol's body."""
        syms = self.index.find_symbol(symbol)
        if not syms:
            return CommandResult(f"(symbol `{symbol}` not in index)", {"kind": "miss"})
        out = []
        for s in syms:
            p = Path(s.file)
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                body = "\n".join(lines[s.start_line - 1:s.end_line])
            except OSError:
                body = s.signature
            out.append(f"# {s.kind} {s.name} — {s.file}:{s.start_line}\n```\n{body}\n```")
        text = "\n\n".join(out)
        return CommandResult(text, {"kind": "symbol", "count": len(syms),
                                    "tokens": count_tokens(text, self.model)})

    def diff_mode(self) -> CommandResult:
        self.diff_mode_on = True
        return CommandResult(
            "Diff-only output mode ON.\n" + patchmod.DIFF_INSTRUCTIONS,
            {"diff_mode": True},
        )

    def compress(self, history: list[Turn] | None = None) -> CommandResult:
        history = history or []
        result = self.memory.compress(history)
        return CommandResult(
            f"Distilled {result['compressed']} turns into the decision log; "
            f"{result['kept_verbatim']} kept verbatim.",
            result | {"log_preview": self.memory.render_log(limit=5)},
        )

    def cost(self) -> CommandResult:
        s = self.telemetry.summary()
        if s.get("requests", 0) == 0:
            return CommandResult("No requests recorded yet.", s)
        text = (
            f"Requests: {s['requests']}\n"
            f"Tokens in: {s['fresh_in']} fresh + {s['cached_in']} cached "
            f"({s['cache_hit_rate']:.0%} cache hit)\n"
            f"Tokens out: {s['out']}\n"
            f"Cost: ${s['cost_usd']} (vs ${s['cost_without_cache_usd']} without cache; "
            f"saved ${s['saved_usd']})"
        )
        return CommandResult(text, s)

    def route(self, tier: str = "") -> CommandResult:
        tier = tier.strip().lower()
        valid = {"frontier", "cheap_cloud", "local"}
        if tier not in valid:
            return CommandResult(
                f"Tiers: {', '.join(sorted(valid))}. Current default routing:\n" +
                "\n".join(f"- {t.value}: {self.router.route(t).tier}" for t in Task),
                {"valid": sorted(valid)},
            )
        r = self.router.route(Task.CODEGEN, forced_tier=tier)
        return CommandResult(f"Forcing tier `{tier}` -> model `{r.model}`.",
                             {"tier": tier, "model": r.model})

    def apply_model_output(self, text: str, *, dry_run: bool = False) -> CommandResult:
        outcome = patchmod.apply_patch(self.project.root, text, dry_run=dry_run)
        return CommandResult(outcome.summary(), {"ok": outcome.ok,
                                                 "results": [r.__dict__ for r in outcome.results]})

    def start(self) -> CommandResult:
        """In-chat init (§4.1): build index, inject map, switch on diff mode."""
        counts = self.build_index()
        self.diff_mode_on = True
        m = self.map()
        text = (
            "ctx engine connected.\n"
            f"Indexed {counts['symbols']} symbols across "
            f"{counts['parsed'] + counts['skipped']} files "
            f"({counts['parsed']} parsed, {counts['skipped']} cached).\n"
            "Diff-only output mode ON.\n\n" + m.text
        )
        return CommandResult(text, {"index": counts})
