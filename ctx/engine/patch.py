"""Patch engine — output savings (§3.6).

The model emits edits as SEARCH/REPLACE blocks (Aider-style) or unified diffs,
never whole files. We parse the blocks and apply with fuzzy matching so
whitespace drift in the anchor doesn't fail the patch. On a miss we report the
hunk back so the caller can widen the anchor or re-ask.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---- SEARCH/REPLACE block format ------------------------------------------
#   path/to/file.py
#   <<<<<<< SEARCH
#   old code
#   =======
#   new code
#   >>>>>>> REPLACE

_SR_BLOCK = re.compile(
    r"^(?P<path>[^\n]*?)\n"
    r"<{5,7} SEARCH\n"
    r"(?P<search>.*?)\n?"
    r"={5,7}\n"
    r"(?P<replace>.*?)\n?"
    r">{5,7} REPLACE",
    re.DOTALL | re.MULTILINE,
)


@dataclass
class Hunk:
    path: str
    search: str
    replace: str


@dataclass
class ApplyResult:
    path: str
    ok: bool
    detail: str
    ratio: float = 1.0


@dataclass
class PatchOutcome:
    results: list[ApplyResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results) and bool(self.results)

    def summary(self) -> str:
        good = sum(1 for r in self.results if r.ok)
        lines = [f"{'OK ' if r.ok else 'FAIL'} {r.path}: {r.detail}" for r in self.results]
        return f"{good}/{len(self.results)} hunks applied\n" + "\n".join(lines)


def parse_search_replace(text: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    for m in _SR_BLOCK.finditer(text):
        hunks.append(Hunk(m.group("path").strip(), m.group("search"), m.group("replace")))
    return hunks


def _fuzzy_locate(haystack: str, needle: str, threshold: float) -> tuple[int, int, float] | None:
    """Find the best window in *haystack* matching *needle*. Returns
    (start, end, ratio) on the line grid, or None below *threshold*."""
    if needle == "":
        return (0, 0, 1.0)
    if needle in haystack:
        idx = haystack.index(needle)
        return (idx, idx + len(needle), 1.0)

    hay_lines = haystack.splitlines(keepends=True)
    needle_norm = "\n".join(l.strip() for l in needle.splitlines())
    n = len(needle.splitlines())
    best: tuple[int, int, float] | None = None
    # offsets per line start
    starts = [0]
    for l in hay_lines:
        starts.append(starts[-1] + len(l))
    for i in range(0, max(1, len(hay_lines) - n + 1)):
        window = "".join(hay_lines[i:i + n])
        window_norm = "\n".join(l.strip() for l in window.splitlines())
        ratio = difflib.SequenceMatcher(None, window_norm, needle_norm).ratio()
        if best is None or ratio > best[2]:
            best = (starts[i], starts[min(i + n, len(starts) - 1)], ratio)
    if best and best[2] >= threshold:
        return best
    return None


def apply_hunk(root: Path, hunk: Hunk, *, threshold: float = 0.7,
               dry_run: bool = False) -> ApplyResult:
    target = (Path(root) / hunk.path).resolve()
    # new-file case: empty search => create/overwrite
    if hunk.search.strip() == "" and not target.exists():
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(hunk.replace, encoding="utf-8")
        return ApplyResult(hunk.path, True, "created new file")

    if not target.exists():
        return ApplyResult(hunk.path, False, "file not found", 0.0)

    text = target.read_text(encoding="utf-8", errors="replace")
    loc = _fuzzy_locate(text, hunk.search, threshold)
    if loc is None:
        return ApplyResult(hunk.path, False,
                           "anchor not found (widen SEARCH or re-ask)", 0.0)
    start, end, ratio = loc
    patched = text[:start] + hunk.replace + text[end:]
    if not dry_run:
        target.write_text(patched, encoding="utf-8")
    verb = "would apply" if dry_run else "applied"
    return ApplyResult(hunk.path, True, f"{verb} (anchor match {ratio:.0%})", ratio)


def apply_patch(root: Path, text: str, *, threshold: float = 0.7,
                dry_run: bool = False) -> PatchOutcome:
    """Parse *text* for SEARCH/REPLACE blocks and apply each."""
    outcome = PatchOutcome()
    hunks = parse_search_replace(text)
    if not hunks:
        outcome.results.append(
            ApplyResult("-", False, "no SEARCH/REPLACE blocks found", 0.0))
        return outcome
    for h in hunks:
        outcome.results.append(apply_hunk(Path(root), h, threshold=threshold,
                                          dry_run=dry_run))
    return outcome


# ---- helper: produce a SEARCH/REPLACE block (for prompting/examples) -------

def make_block(path: str, search: str, replace: str) -> str:
    return (f"{path}\n<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE")


DIFF_INSTRUCTIONS = (
    "Output edits ONLY as SEARCH/REPLACE blocks — never whole files. Format:\n"
    "  path/to/file.ext\n"
    "  <<<<<<< SEARCH\n"
    "  exact existing lines to find\n"
    "  =======\n"
    "  replacement lines\n"
    "  >>>>>>> REPLACE\n"
    "Keep SEARCH minimal but unique. For a new file, leave SEARCH empty."
)
