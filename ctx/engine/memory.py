"""Memory store — the Intent store (§3.2).

Keeps the last few turns verbatim (the volatile, high-attention zone) and
distills everything older into an *append-only* decision log of
``{decision, rationale, files_touched, timestamp}``. Append-only is mandatory:
rewriting history would break the prompt-prefix cache.

Distillation routes to a small model (Ollama local, or a cheap cloud tier).
Both are optional; with neither present we fall back to a deterministic
heuristic extractor so the log still grows without a model.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


@dataclass
class Turn:
    role: str          # user | assistant
    content: str
    ts: float = 0.0


@dataclass
class Decision:
    decision: str
    rationale: str
    files_touched: list[str]
    timestamp: float

    def render(self) -> str:
        files = ", ".join(self.files_touched) if self.files_touched else "—"
        return f"- {self.decision} (why: {self.rationale}) [files: {files}]"


class DecisionLog:
    """Append-only JSONL log. Never rewritten — only appended or read."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, d: Decision) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(d), ensure_ascii=False) + "\n")

    def all(self) -> list[Decision]:
        if not self.path.exists():
            return []
        out: list[Decision] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Decision(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def render(self, limit: int | None = None) -> str:
        entries = self.all()
        if limit is not None:
            entries = entries[-limit:]
        if not entries:
            return "# Decision log (empty)"
        body = "\n".join(d.render() for d in entries)
        return "# Decision log (append-only — the *why* behind the code)\n" + body


# ---------------------------------------------------------------------------
# Distillation
# ---------------------------------------------------------------------------

_DISTILL_PROMPT = (
    "Compress the conversation turns below into a single decision record. "
    "Output strict JSON: {\"decision\": str, \"rationale\": str, "
    "\"files_touched\": [str]}. Capture the *why*, not chit-chat.\n\nTURNS:\n"
)


def _ollama_distill(turns: list[Turn], model: str) -> Decision | None:
    try:
        import httpx
    except ImportError:
        return None
    text = "\n".join(f"{t.role}: {t.content}" for t in turns)
    try:
        resp = httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": _DISTILL_PROMPT + text,
                  "stream": False, "format": "json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = json.loads(resp.json()["response"])
        return Decision(
            decision=str(data.get("decision", "")).strip(),
            rationale=str(data.get("rationale", "")).strip(),
            files_touched=[str(f) for f in data.get("files_touched", [])],
            timestamp=time.time(),
        )
    except Exception:
        return None


_FILE_RE = None


def _heuristic_distill(turns: list[Turn]) -> Decision:
    import re
    global _FILE_RE
    if _FILE_RE is None:
        _FILE_RE = re.compile(r"[\w./\\-]+\.\w{1,5}")
    files: list[str] = []
    for t in turns:
        for m in _FILE_RE.findall(t.content):
            if m not in files and "." in m:
                files.append(m)
    # first user ask = decision seed; first assistant line = rationale seed.
    user_line = next((t.content for t in turns if t.role == "user"), "")
    asst_line = next((t.content for t in turns if t.role == "assistant"), "")
    decision = (user_line.strip().splitlines() or [""])[0][:160]
    rationale = (asst_line.strip().splitlines() or [""])[0][:160]
    return Decision(
        decision=decision or "(turn distilled)",
        rationale=rationale or "(no rationale captured)",
        files_touched=files[:10],
        timestamp=time.time(),
    )


class MemoryStore:
    """Verbatim window + append-only decision log, with distillation."""

    def __init__(self, log_path: Path, *, verbatim_keep: int = 4,
                 local_model: str = "qwen2.5:1.5b") -> None:
        self.log = DecisionLog(log_path)
        self.verbatim_keep = verbatim_keep
        self.local_model = local_model

    def distill_turns(self, turns: list[Turn]) -> Decision:
        """Distill *turns* into one decision record (model or heuristic)."""
        d = _ollama_distill(turns, self.local_model) if turns else None
        if d is None or not d.decision:
            d = _heuristic_distill(turns)
        return d

    def compress(self, history: list[Turn]) -> dict[str, object]:
        """Fold all-but-recent turns into the log; return what stays verbatim."""
        if len(history) <= self.verbatim_keep:
            return {"compressed": 0, "kept_verbatim": len(history),
                    "recent": history}
        older = history[: -self.verbatim_keep]
        recent = history[-self.verbatim_keep:]
        d = self.distill_turns(older)
        self.log.append(d)
        return {"compressed": len(older), "kept_verbatim": len(recent),
                "recent": recent, "decision": d}

    def render_log(self, limit: int | None = None) -> str:
        return self.log.render(limit)
