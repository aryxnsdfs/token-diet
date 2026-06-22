"""Model router (§3.7).

Reserve the frontier model for reasoning and code generation. Route mechanical
work — summarization, intent classification, decision-log distillation, commit
messages, relevance pre-filtering — to a local Ollama model or a cheap cloud
tier. Same result, large cost cut.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import ModelTiers


class Task(str, Enum):
    REASONING = "reasoning"
    CODEGEN = "codegen"
    SUMMARIZE = "summarize"
    CLASSIFY = "classify"
    DISTILL = "distill"
    COMMIT_MSG = "commit_msg"
    RELEVANCE = "relevance"


# Which tier each task class belongs to.
_FRONTIER = {Task.REASONING, Task.CODEGEN}
_LOCAL_OK = {Task.SUMMARIZE, Task.CLASSIFY, Task.DISTILL, Task.COMMIT_MSG, Task.RELEVANCE}


@dataclass
class Route:
    task: Task
    tier: str          # frontier | cheap_cloud | local
    model: str
    reason: str


class Router:
    def __init__(self, tiers: ModelTiers, *, prefer_local: bool = True) -> None:
        self.tiers = tiers
        self.prefer_local = prefer_local

    def route(self, task: Task, *, forced_tier: str | None = None) -> Route:
        if forced_tier:
            model = getattr(self.tiers, forced_tier, self.tiers.frontier)
            return Route(task, forced_tier, model, "forced by /route")
        if task in _FRONTIER:
            return Route(task, "frontier", self.tiers.frontier,
                         "reasoning/codegen needs the frontier model")
        if task in _LOCAL_OK:
            if self.prefer_local:
                return Route(task, "local", self.tiers.local,
                             "mechanical task -> local ollama (zero cloud cost)")
            return Route(task, "cheap_cloud", self.tiers.cheap_cloud,
                         "mechanical task -> cheap cloud tier")
        return Route(task, "frontier", self.tiers.frontier, "unknown task -> safe default")
