"""Per-project paths and config.

Engine state lives under ``<project>/.ctx/``. Everything here is regenerable;
the directory is gitignored. ``config.toml`` holds budgets and model tiers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:  # py3.11+ stdlib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

CTX_DIRNAME = ".ctx"


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from *start* to the nearest dir containing ``.ctx`` or ``.git``.

    Falls back to *start* (cwd) when neither marker is found.
    """
    start = (start or Path.cwd()).resolve()
    for parent in [start, *start.parents]:
        if (parent / CTX_DIRNAME).exists() or (parent / ".git").exists():
            return parent
    return start


@dataclass
class Budgets:
    """Token budget per assembler layer (fractions of the model window)."""

    window: int = 200_000          # model context window
    generation_reserve: int = 8_000  # held back for the reply
    repo_map: float = 0.20         # share of remaining budget
    decision_log: float = 0.10
    snippets: float = 0.35
    verbatim: float = 0.35


@dataclass
class ModelTiers:
    frontier: str = "claude-opus-4-8"
    cheap_cloud: str = "claude-haiku-4-5-20251001"
    local: str = "qwen2.5:1.5b"  # via ollama


@dataclass
class Config:
    budgets: Budgets = field(default_factory=Budgets)
    models: ModelTiers = field(default_factory=ModelTiers)
    enabled_commands: list[str] = field(default_factory=list)  # empty = all
    tokenizer_model: str = "claude-opus-4-8"

    def to_toml(self) -> str:
        b, m = self.budgets, self.models
        cmds = ", ".join(f'"{c}"' for c in self.enabled_commands)
        return (
            "# ctx project config — edit budgets and model tiers here.\n\n"
            f'tokenizer_model = "{self.tokenizer_model}"\n'
            f"enabled_commands = [{cmds}]\n\n"
            "[budgets]\n"
            f"window = {b.window}\n"
            f"generation_reserve = {b.generation_reserve}\n"
            f"repo_map = {b.repo_map}\n"
            f"decision_log = {b.decision_log}\n"
            f"snippets = {b.snippets}\n"
            f"verbatim = {b.verbatim}\n\n"
            "[models]\n"
            f'frontier = "{m.frontier}"\n'
            f'cheap_cloud = "{m.cheap_cloud}"\n'
            f'local = "{m.local}"\n'
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Config":
        budgets = Budgets(**(d.get("budgets") or {}))
        models = ModelTiers(**(d.get("models") or {}))
        return cls(
            budgets=budgets,
            models=models,
            enabled_commands=list(d.get("enabled_commands") or []),
            tokenizer_model=d.get("tokenizer_model", "claude-opus-4-8"),
        )


class Project:
    """Resolves and owns per-project ``.ctx`` paths."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = (Path(root) if root is not None else find_project_root()).resolve()
        self.ctx_dir = self.root / CTX_DIRNAME
        self.index_db = self.ctx_dir / "index.db"
        self.config_path = self.ctx_dir / "config.toml"
        self.decisions = self.ctx_dir / "decisions.jsonl"
        self.cache_dir = self.ctx_dir / "cache"

    def ensure_dirs(self) -> None:
        self.ctx_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> Config:
        if self.config_path.exists() and tomllib is not None:
            with open(self.config_path, "rb") as fh:
                return Config.from_dict(tomllib.load(fh))
        return Config()

    def write_config(self, cfg: Config) -> None:
        self.ensure_dirs()
        self.config_path.write_text(cfg.to_toml(), encoding="utf-8")

    def initialized(self) -> bool:
        return self.ctx_dir.exists()

    def as_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in asdict_paths(self).items()}


def asdict_paths(p: Project) -> dict[str, Path]:
    return {
        "root": p.root,
        "ctx_dir": p.ctx_dir,
        "index_db": p.index_db,
        "config_path": p.config_path,
        "decisions": p.decisions,
        "cache_dir": p.cache_dir,
    }
