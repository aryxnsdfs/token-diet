"""Unit tests for the engine core. Run with `pytest`."""

from __future__ import annotations

from pathlib import Path

import pytest

from ctx.config import Project, Config
from ctx.engine.budget import TextItem, count_tokens, fit_to_budget
from ctx.engine.index import CodeIndex, parse_symbols
from ctx.engine.memory import MemoryStore, Turn
from ctx.engine import patch as patchmod
from ctx.engine.repomap import render_map
from ctx.engine.facade import Engine


SAMPLE_PY = '''\
import os

GREETING = "hi"

class Greeter:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return format_msg(self.name)

def format_msg(name):
    return GREETING + " " + name
'''


def test_budget_counts_positive():
    assert count_tokens("hello world") > 0


def test_fit_to_budget_demotes():
    items = [
        TextItem(rungs=["a" * 400, "short", "x"], relevance=0.1, priority=0),
        TextItem(rungs=["b" * 400, "keep-rich"], relevance=10.0, priority=5),
    ]
    report = fit_to_budget(items, budget=30)
    assert report.tokens_after <= 30 or report.notes
    # low-relevance item should have demoted further than the high-relevance one
    assert items[0].level >= items[1].level


def test_parse_symbols_finds_class_and_funcs(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(SAMPLE_PY, encoding="utf-8")
    symbols, edges = parse_symbols(f, SAMPLE_PY)
    names = {s.name for s in symbols}
    assert "Greeter" in names
    assert "format_msg" in names
    kinds = {s.name: s.kind for s in symbols}
    assert kinds["Greeter"] == "class"


def test_index_caches_unchanged(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(SAMPLE_PY, encoding="utf-8")
    idx = CodeIndex(tmp_path / "index.db")
    assert idx.index_file(f) is True          # first parse
    assert idx.index_file(f) is False         # unchanged -> cache hit
    f.write_text(SAMPLE_PY + "\n# edit\n", encoding="utf-8")
    assert idx.index_file(f) is True          # changed -> reparse
    idx.close()


def test_repo_map_ranks_within_budget(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(SAMPLE_PY, encoding="utf-8")
    idx = CodeIndex(tmp_path / "index.db")
    idx.index_file(f)
    out = render_map(idx, token_budget=200, mentioned=["format_msg"])
    assert "format_msg" in out
    assert count_tokens(out) <= 260  # budget + header slack
    idx.close()


def test_decision_log_append_only(tmp_path):
    store = MemoryStore(tmp_path / "decisions.jsonl", verbatim_keep=2)
    history = [
        Turn("user", "change auth to use bcrypt in auth.py"),
        Turn("assistant", "done, edited auth.py hashing"),
        Turn("user", "now add tests"),
        Turn("assistant", "added tests/test_auth.py"),
        Turn("user", "latest message"),
    ]
    result = store.compress(history)
    assert result["compressed"] == 3
    assert result["kept_verbatim"] == 2
    assert len(store.log.all()) == 1


def test_patch_search_replace_fuzzy(tmp_path):
    target = tmp_path / "f.py"
    target.write_text("def add(a, b):\n    return a+b\n", encoding="utf-8")
    block = patchmod.make_block(
        "f.py",
        "def add(a,  b):\n    return a+b",   # note drifted whitespace in anchor
        "def add(a, b):\n    return a + b",
    )
    outcome = patchmod.apply_patch(tmp_path, block)
    assert outcome.ok, outcome.summary()
    assert "a + b" in target.read_text(encoding="utf-8")


def test_patch_new_file(tmp_path):
    block = patchmod.make_block("new/created.py", "", "print('hi')\n")
    outcome = patchmod.apply_patch(tmp_path, block)
    assert outcome.ok
    assert (tmp_path / "new" / "created.py").exists()


def test_engine_end_to_end(tmp_path):
    (tmp_path / "mod.py").write_text(SAMPLE_PY, encoding="utf-8")
    project = Project(tmp_path)
    project.write_config(Config())
    engine = Engine(project)
    counts = engine.build_index()
    assert counts["symbols"] > 0

    m = engine.map()
    assert "Greeter" in m.text or "format_msg" in m.text

    e = engine.explain("format_msg")
    assert "format_msg" in e.text

    d = engine.diff_mode()
    assert engine.diff_mode_on is True
    assert "SEARCH" in d.text
