"""Token measurement + budget fitting with graceful demotion.

Rule §2.4: measure with the *real* tokenizer, never folklore. We wrap
``tiktoken`` when present and fall back to a calibrated char estimate so the
engine stays runnable with zero heavy deps installed.

Rule §3.4: fit by *demoting* the worst value-density items rather than hard
truncating.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_ENCODER_CACHE: dict[str, Callable[[str], int]] = {}


def _tiktoken_counter(model: str) -> Callable[[str], int] | None:
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        enc = tiktoken.encoding_for_model(model)
    except Exception:
        # Claude / unknown model: cl100k is a close-enough proxy for budgeting.
        enc = tiktoken.get_encoding("cl100k_base")
    return lambda text: len(enc.encode(text))


def _estimate_counter() -> Callable[[str], int]:
    # ~4 chars/token for English+code is the standard rough proxy. We also
    # round up so the estimate never *under*-counts the cache prefix.
    return lambda text: max(1, math.ceil(len(text) / 4))


def counter_for(model: str = "claude-opus-4-8") -> Callable[[str], int]:
    """Return a ``text -> token_count`` function for *model* (cached)."""
    if model not in _ENCODER_CACHE:
        _ENCODER_CACHE[model] = _tiktoken_counter(model) or _estimate_counter()
    return _ENCODER_CACHE[model]


def count_tokens(text: str, model: str = "claude-opus-4-8") -> int:
    return counter_for(model)(text)


def using_real_tokenizer(model: str = "claude-opus-4-8") -> bool:
    """True when a real tokenizer backs *model* (else the char estimate)."""
    counter_for(model)  # populate cache
    try:
        import tiktoken  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Demotable items + budget fitting
# ---------------------------------------------------------------------------


class Demotable(Protocol):
    """An item that can shed detail (body -> signature -> name+pointer)."""

    relevance: float

    def render(self) -> str: ...
    def tokens(self, model: str) -> int: ...
    def can_demote(self) -> bool: ...
    def demote(self) -> None: ...
    def droppable(self) -> bool: ...


@dataclass
class TextItem:
    """Concrete demotable: a stack of rungs from richest to poorest.

    ``rungs[0]`` is the full body; each step down is cheaper but lossier. The
    final rung is typically a one-line pointer the model can re-expand with a
    follow-up command (e.g. ``/explain``).
    """

    rungs: list[str]
    relevance: float = 1.0
    priority: int = 0  # lower drops first when demotion is exhausted
    level: int = 0

    def render(self) -> str:
        return self.rungs[self.level]

    def tokens(self, model: str = "claude-opus-4-8") -> int:
        return count_tokens(self.render(), model)

    def can_demote(self) -> bool:
        return self.level < len(self.rungs) - 1

    def demote(self) -> None:
        if self.can_demote():
            self.level += 1

    def droppable(self) -> bool:
        return self.priority <= 0


@dataclass
class FitReport:
    fit: bool
    tokens_before: int
    tokens_after: int
    demotions: int
    drops: int
    notes: list[str] = field(default_factory=list)


def measure(items: Iterable[Demotable], model: str = "claude-opus-4-8") -> int:
    return sum(i.tokens(model) for i in items)


def fit_to_budget(
    items: list[TextItem],
    budget: int,
    model: str = "claude-opus-4-8",
) -> FitReport:
    """Shrink *items* in place to fit *budget* tokens.

    Greedy: repeatedly demote the worst value-density item (lowest
    ``relevance / tokens``). When nothing can demote, drop the lowest-priority
    droppable item. Hard truncation never happens here — callers truncate only
    as a documented last resort.
    """
    before = measure(items, model)
    demotions = drops = 0
    notes: list[str] = []

    def worst_demotable() -> TextItem | None:
        cands = [i for i in items if i.can_demote()]
        if not cands:
            return None
        return min(cands, key=lambda i: i.relevance / max(1, i.tokens(model)))

    while measure(items, model) > budget:
        victim = worst_demotable()
        if victim is not None:
            victim.demote()
            demotions += 1
            continue
        droppable = [i for i in items if i.droppable()]
        if droppable:
            target = min(droppable, key=lambda i: (i.priority, i.relevance))
            items.remove(target)
            drops += 1
            continue
        notes.append("budget exceeded; nothing left to demote or drop")
        break

    after = measure(items, model)
    return FitReport(
        fit=after <= budget,
        tokens_before=before,
        tokens_after=after,
        demotions=demotions,
        drops=drops,
        notes=notes,
    )
