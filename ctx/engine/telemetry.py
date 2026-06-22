"""Token + cache telemetry (§9 guardrail metrics).

Per-request log of tokens in/out, cache-hit rate, model, and estimated cost.
Backs the `/cost` command. SQLite-free: a small JSONL alongside the index.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path

# Rough public list prices (USD per 1M tokens). Verify against §10 before
# trusting for billing — these are for relative telemetry only.
PRICES = {
    "claude-opus-4-8":        {"in": 15.0, "cached_in": 1.5, "out": 75.0},
    "claude-sonnet-4-6":      {"in": 3.0,  "cached_in": 0.3, "out": 15.0},
    "claude-haiku-4-5-20251001": {"in": 0.8, "cached_in": 0.08, "out": 4.0},
}
_DEFAULT_PRICE = {"in": 3.0, "cached_in": 0.3, "out": 15.0}


@dataclass
class RequestStat:
    ts: float
    model: str
    fresh_in: int
    cached_in: int
    out: int
    cache_hit_rate: float
    cost_usd: float
    command: str = ""


def estimate_cost(model: str, fresh_in: int, cached_in: int, out: int) -> float:
    p = PRICES.get(model, _DEFAULT_PRICE)
    return (fresh_in * p["in"] + cached_in * p["cached_in"] + out * p["out"]) / 1_000_000


class Telemetry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, *, model: str, fresh_in: int, cached_in: int, out: int,
               command: str = "") -> RequestStat:
        total_in = fresh_in + cached_in
        hit = (cached_in / total_in) if total_in else 0.0
        stat = RequestStat(
            ts=time.time(), model=model, fresh_in=fresh_in, cached_in=cached_in,
            out=out, cache_hit_rate=hit,
            cost_usd=estimate_cost(model, fresh_in, cached_in, out), command=command,
        )
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(stat)) + "\n")
        return stat

    def all(self) -> list[RequestStat]:
        if not self.path.exists():
            return []
        out: list[RequestStat] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(RequestStat(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        return out

    def summary(self) -> dict:
        stats = self.all()
        if not stats:
            return {"requests": 0}
        fresh = sum(s.fresh_in for s in stats)
        cached = sum(s.cached_in for s in stats)
        out = sum(s.out for s in stats)
        cost = sum(s.cost_usd for s in stats)
        total_in = fresh + cached
        # cost if nothing were cached (cached tokens billed at full input rate)
        naive = sum(estimate_cost(s.model, s.fresh_in + s.cached_in, 0, s.out)
                    for s in stats)
        return {
            "requests": len(stats),
            "fresh_in": fresh,
            "cached_in": cached,
            "out": out,
            "cache_hit_rate": (cached / total_in) if total_in else 0.0,
            "cost_usd": round(cost, 4),
            "cost_without_cache_usd": round(naive, 4),
            "saved_usd": round(naive - cost, 4),
        }
