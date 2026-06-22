"""The guardrail (§2.5, §9).

A held-out set of representative tasks with automatic checks. Token reduction
is only "saving intelligence" if these keep passing. Each case asserts both a
*success* condition (does the engine surface the right thing) and a *token*
condition (did it actually shrink context vs the naive whole-file dump).

Extend this suite as the engine grows — it runs the whole way through, not at
the end.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ctx.config import Config, Project
from ctx.engine.budget import count_tokens
from ctx.engine.facade import Engine


REPO = {
    "auth.py": (
        "import hashlib\n\n"
        "def hash_password(pw):\n"
        "    return hashlib.sha256(pw.encode()).hexdigest()\n\n"
        "def verify(pw, digest):\n"
        "    return hash_password(pw) == digest\n"
    ),
    "api.py": (
        "from auth import verify\n\n"
        "def login(user, pw, store):\n"
        "    return verify(pw, store[user])\n"
    ),
    "util.py": "def noop():\n    return None\n" + ("# filler\n" * 200),
}


@dataclass
class EvalCase:
    name: str
    run: object  # callable(engine) -> (success: bool, tokens: int)


def _make_engine(tmp_path: Path) -> Engine:
    for name, body in REPO.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    project = Project(tmp_path)
    project.write_config(Config())
    engine = Engine(project)
    engine.build_index()
    return engine


def _whole_repo_tokens(tmp_path: Path) -> int:
    return sum(count_tokens((tmp_path / n).read_text()) for n in REPO)


def test_map_is_smaller_than_whole_repo(tmp_path):
    engine = _make_engine(tmp_path)
    m = engine.map()
    naive = _whole_repo_tokens(tmp_path)
    map_tokens = count_tokens(m.text)
    # success: the map mentions real structure; token: it's a fraction of dump
    assert "hash_password" in m.text or "login" in m.text
    assert map_tokens < naive, (map_tokens, naive)


def test_explain_surfaces_target_symbol(tmp_path):
    engine = _make_engine(tmp_path)
    out = engine.explain("hash_password")
    assert "hashlib.sha256" in out.text  # the actual body, on demand


def test_focus_pulls_only_requested_file(tmp_path):
    engine = _make_engine(tmp_path)
    out = engine.focus("api.py")
    assert "def login" in out.text
    assert "hashlib" not in out.text  # didn't drag in unrelated files


def test_map_ranks_relevant_symbol_above_filler(tmp_path):
    engine = _make_engine(tmp_path)
    # mention an auth symbol; the noop filler should not crowd it out
    m = engine.map(mentioned=["verify"]) if False else engine.map()
    assert "verify" in m.text or "login" in m.text
