"""Graph-ranked repo map (§3.1).

Builds a directed symbol graph from the index and runs a *personalized*
PageRank seeded by the user's current focus (open files, mentioned symbols,
recently edited files). Emits top-ranked signatures until a token budget fills,
so the map is intelligent — relevant structure first — rather than a flat dump.

Uses ``networkx`` when available; falls back to a small pure-Python power
iteration so the map still ranks without the dependency.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .budget import count_tokens
from .index import CodeIndex, Symbol


@dataclass
class MapEntry:
    symbol: Symbol
    score: float


def _build_adjacency(index: CodeIndex) -> tuple[dict[str, set[str]], dict[str, list[Symbol]]]:
    """Return name->referenced-names and name->defining-symbols."""
    by_name: dict[str, list[Symbol]] = defaultdict(list)
    for s in index.all_symbols():
        by_name[s.name].append(s)

    adj: dict[str, set[str]] = defaultdict(set)
    known = set(by_name)
    for e in index.all_edges():
        if e.kind == "calls" and e.dst in known and e.src in known:
            adj[e.src].add(e.dst)
    return adj, by_name


def _personalized_pagerank(
    nodes: list[str],
    adj: dict[str, set[str]],
    seeds: dict[str, float],
    *,
    damping: float = 0.85,
    iterations: int = 40,
) -> dict[str, float]:
    try:
        import networkx as nx

        g = nx.DiGraph()
        g.add_nodes_from(nodes)
        for src, dsts in adj.items():
            for dst in dsts:
                g.add_edge(src, dst)
        if not g.number_of_nodes():
            return {}
        pers = seeds or None
        try:
            return nx.pagerank(g, alpha=damping, personalization=pers, max_iter=200)
        except Exception:
            return nx.pagerank(g, alpha=damping, max_iter=200)
    except ImportError:
        return _power_iteration(nodes, adj, seeds, damping=damping, iterations=iterations)


def _power_iteration(nodes, adj, seeds, *, damping, iterations):
    n = len(nodes)
    if n == 0:
        return {}
    total_seed = sum(seeds.values()) or 0.0
    if total_seed > 0:
        teleport = {k: seeds.get(k, 0.0) / total_seed for k in nodes}
    else:
        teleport = {k: 1.0 / n for k in nodes}
    rank = {k: 1.0 / n for k in nodes}
    out_deg = {k: len(adj.get(k, ())) for k in nodes}
    for _ in range(iterations):
        nxt = {k: (1 - damping) * teleport[k] for k in nodes}
        dangling = sum(rank[k] for k in nodes if out_deg[k] == 0)
        for k in nodes:
            nxt[k] += damping * dangling * teleport[k]
        for src in nodes:
            if out_deg[src]:
                share = damping * rank[src] / out_deg[src]
                for dst in adj[src]:
                    nxt[dst] += share
        rank = nxt
    return rank


def rank_symbols(
    index: CodeIndex,
    *,
    focus_files: Iterable[str] = (),
    mentioned: Iterable[str] = (),
    recent_files: Iterable[str] = (),
) -> list[MapEntry]:
    """Rank all symbols by personalized PageRank around the focus set."""
    adj, by_name = _build_adjacency(index)
    nodes = list(by_name.keys())
    if not nodes:
        return []

    focus_set = {str(f) for f in focus_files}
    recent_set = {str(f) for f in recent_files}
    mentioned_set = {m for m in mentioned}

    seeds: dict[str, float] = defaultdict(float)
    for name, syms in by_name.items():
        if name in mentioned_set:
            seeds[name] += 3.0
        for s in syms:
            if s.file in focus_set:
                seeds[name] += 2.0
            if s.file in recent_set:
                seeds[name] += 1.0

    scores = _personalized_pagerank(nodes, adj, dict(seeds))
    entries: list[MapEntry] = []
    for name, syms in by_name.items():
        sc = scores.get(name, 0.0)
        # prefer the richest definition (class/function over a bare variable)
        s = sorted(syms, key=lambda x: x.kind != "variable")[0]
        entries.append(MapEntry(s, sc))
    entries.sort(key=lambda e: e.score, reverse=True)
    return entries


def render_map(
    index: CodeIndex,
    *,
    token_budget: int = 4000,
    model: str = "claude-opus-4-8",
    focus_files: Iterable[str] = (),
    mentioned: Iterable[str] = (),
    recent_files: Iterable[str] = (),
) -> str:
    """Emit a compact, file-grouped map of top-ranked signatures within budget."""
    entries = rank_symbols(
        index, focus_files=focus_files, mentioned=mentioned, recent_files=recent_files
    )
    if not entries:
        return "(repo map empty — run `ctx index` first)"

    # Group selected entries by file, preserving global rank order.
    chosen: list[MapEntry] = []
    header = "# Repo map (graph-ranked signatures)\n"
    used = count_tokens(header, model)
    for e in entries:
        line = f"  {e.symbol.signature}"
        cost = count_tokens(line, model) + 2
        if used + cost > token_budget:
            break
        chosen.append(e)
        used += cost

    by_file: dict[str, list[MapEntry]] = defaultdict(list)
    for e in chosen:
        by_file[e.symbol.file].append(e)

    out = [header.rstrip()]
    for f in sorted(by_file):
        out.append(f"\n{f}")
        for e in sorted(by_file[f], key=lambda x: x.symbol.start_line):
            tag = {"class": "class", "function": "def", "method": "def",
                   "variable": "var", "import": "import"}.get(e.symbol.kind, "")
            out.append(f"  {e.symbol.signature}")
    remaining = len(entries) - len(chosen)
    if remaining > 0:
        out.append(f"\n… {remaining} more symbols (use /show or /lookup to pull detail)")
    return "\n".join(out)
