"""Context assembler — the heart (§3.4).

Builds the prompt in layers ordered for *caching first, attention second*:
stable content at the top (cached prefix), most relevant + most recent content
at the bottom (attention sweet spot).

| Order | Layer                         | Volatility   | Cached? |
|-------|-------------------------------|--------------|---------|
| 1     | System prompt + tool schemas  | session      | yes     |
| 2     | Graph-ranked repo map         | on file edit | yes     |
| 3     | Decision log                  | append-only  | yes     |
| 4     | Retrieved snippets (this task)| per turn     | no      |
| 5     | Recent verbatim turns         | per turn     | no      |
| 6     | New user message              | per turn     | no      |

The one rule: keep the prefix append-only so the cache stays warm. Cached
layers precede volatile ones; never reorder or edit inside the cached region.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from ..config import Budgets
from .budget import TextItem, count_tokens, fit_to_budget, using_real_tokenizer
from .memory import Turn


class Layer(IntEnum):
    SYSTEM = 1
    REPO_MAP = 2
    DECISION_LOG = 3
    SNIPPETS = 4
    VERBATIM = 5
    USER = 6


CACHED_LAYERS = {Layer.SYSTEM, Layer.REPO_MAP, Layer.DECISION_LOG}


@dataclass
class Block:
    layer: Layer
    text: str
    cached: bool
    tokens: int


@dataclass
class AssembledPrompt:
    blocks: list[Block]
    total_tokens: int
    cached_tokens: int
    fresh_tokens: int
    cache_breakpoint_after: Layer | None
    real_tokenizer: bool
    notes: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks if b.text.strip())

    def to_messages(self) -> list[dict]:
        """Anthropic-style messages with a cache_control breakpoint on the
        last cached block (the stable prefix)."""
        sys_parts = [b for b in self.blocks if b.cached]
        convo = [b for b in self.blocks if not b.cached]
        system: list[dict] = []
        for i, b in enumerate(sys_parts):
            part: dict = {"type": "text", "text": b.text}
            if i == len(sys_parts) - 1:  # breakpoint on last cached part
                part["cache_control"] = {"type": "ephemeral"}
            system.append(part)
        user_text = "\n\n".join(b.text for b in convo)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]


def _alloc(budgets: Budgets) -> dict[Layer, int]:
    avail = max(1, budgets.window - budgets.generation_reserve)
    return {
        Layer.REPO_MAP: int(avail * budgets.repo_map),
        Layer.DECISION_LOG: int(avail * budgets.decision_log),
        Layer.SNIPPETS: int(avail * budgets.snippets),
        Layer.VERBATIM: int(avail * budgets.verbatim),
    }


class Assembler:
    def __init__(self, budgets: Budgets, model: str = "claude-opus-4-8") -> None:
        self.budgets = budgets
        self.model = model

    def assemble(
        self,
        *,
        system: str,
        repo_map: str = "",
        decision_log: str = "",
        snippets: list[TextItem] | None = None,
        verbatim: list[Turn] | None = None,
        user_message: str = "",
    ) -> AssembledPrompt:
        alloc = _alloc(self.budgets)
        notes: list[str] = []
        snippets = snippets or []
        verbatim = verbatim or []

        # Layers 4 & 5 are budget-fit with graceful demotion.
        snip_report = fit_to_budget(list(snippets), alloc[Layer.SNIPPETS], self.model)
        if snip_report.demotions or snip_report.drops:
            notes.append(
                f"snippets: {snip_report.demotions} demotions, "
                f"{snip_report.drops} drops to fit {alloc[Layer.SNIPPETS]} tok")
        snippet_text = "\n\n".join(s.render() for s in snippets if s.render().strip())

        verbatim_text = self._fit_verbatim(verbatim, alloc[Layer.VERBATIM], notes)

        # repo map / decision log: truncate-at-boundary only if over (they are
        # produced within budget upstream, this is a backstop).
        repo_map = self._clip(repo_map, alloc[Layer.REPO_MAP], "repo map", notes)
        decision_log = self._clip(decision_log, alloc[Layer.DECISION_LOG],
                                  "decision log", notes)

        raw = [
            (Layer.SYSTEM, system, True),
            (Layer.REPO_MAP, repo_map, True),
            (Layer.DECISION_LOG, decision_log, True),
            (Layer.SNIPPETS, snippet_text, False),
            (Layer.VERBATIM, verbatim_text, False),
            (Layer.USER, user_message, False),
        ]
        blocks: list[Block] = []
        for layer, text, cached in raw:
            if not text.strip():
                continue
            blocks.append(Block(layer, text, cached, count_tokens(text, self.model)))

        cached_tokens = sum(b.tokens for b in blocks if b.cached)
        fresh_tokens = sum(b.tokens for b in blocks if not b.cached)
        last_cached = max((b.layer for b in blocks if b.cached), default=None)

        return AssembledPrompt(
            blocks=blocks,
            total_tokens=cached_tokens + fresh_tokens,
            cached_tokens=cached_tokens,
            fresh_tokens=fresh_tokens,
            cache_breakpoint_after=last_cached,
            real_tokenizer=using_real_tokenizer(self.model),
            notes=notes,
        )

    def _fit_verbatim(self, turns: list[Turn], budget: int, notes: list[str]) -> str:
        # Keep the most recent turns; drop oldest first (attention favors recent).
        kept: list[Turn] = []
        used = 0
        for t in reversed(turns):
            chunk = f"{t.role}: {t.content}"
            cost = count_tokens(chunk, self.model)
            if used + cost > budget and kept:
                notes.append(f"verbatim: dropped {len(turns) - len(kept)} oldest turns")
                break
            kept.append(t)
            used += cost
        kept.reverse()
        return "\n\n".join(f"{t.role}: {t.content}" for t in kept)

    def _clip(self, text: str, budget: int, label: str, notes: list[str]) -> str:
        if not text:
            return text
        if count_tokens(text, self.model) <= budget:
            return text
        # clip on line boundaries from the end (keep the head — most relevant)
        lines = text.splitlines()
        out: list[str] = []
        used = 0
        for ln in lines:
            c = count_tokens(ln, self.model)
            if used + c > budget:
                break
            out.append(ln)
            used += c
        notes.append(f"{label}: clipped to {budget} tok (backstop)")
        return "\n".join(out) + "\n… (clipped)"
