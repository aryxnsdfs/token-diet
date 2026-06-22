"""Code index — the Structure store (§3.1).

Parses changed files into symbols (classes, functions, top-level vars, imports),
drops bodies and comments, and records a reference graph. SQLite holds the
symbol table, the edge list, and a content hash per file so unchanged files are
never re-parsed.

Parsing strategy: prefer ``tree-sitter`` (via ``tree_sitter_language_pack``)
when installed; otherwise fall back to a regex extractor so the engine is
runnable with no native grammars. The schema is identical either way.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

# Extension -> tree-sitter language name (also gates which files we walk).
LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
}

IGNORE_DIRS = {
    ".git", ".ctx", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".pytest_cache", "target", ".idea", ".vscode",
}


@dataclass
class Symbol:
    name: str
    kind: str          # class | function | method | variable | import
    file: str
    start_line: int
    end_line: int
    signature: str
    docstring: str = ""


@dataclass
class Edge:
    src: str           # symbol name (or "<module>")
    dst: str           # referenced name
    kind: str          # calls | imports | references
    file: str


def file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _load_ts_parser(lang: str):
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None
    try:
        return get_parser(lang)
    except Exception:
        return None


def parse_symbols(path: Path, text: str) -> tuple[list[Symbol], list[Edge]]:
    lang = LANG_BY_EXT.get(path.suffix.lower())
    if lang:
        parser = _load_ts_parser(lang)
        if parser is not None:
            try:
                return _parse_tree_sitter(parser, lang, path, text)
            except Exception:
                pass  # fall through to regex
    return _parse_regex(path, text)


def _node_text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _parse_tree_sitter(parser, lang: str, path: Path, text: str):
    src = text.encode("utf-8", "replace")
    tree = parser.parse(src)
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    rel = str(path)

    def first_line(node) -> str:
        snippet = _node_text(src, node).splitlines()
        return snippet[0].strip() if snippet else ""

    def name_child(node) -> str:
        n = node.child_by_field_name("name")
        return _node_text(src, n) if n is not None else ""

    func_types = {"function_definition", "function_declaration", "method_definition",
                  "function_item", "method_declaration", "arrow_function"}
    class_types = {"class_definition", "class_declaration", "struct_item",
                   "impl_item", "interface_declaration"}
    call_types = {"call", "call_expression", "method_invocation"}
    import_types = {"import_statement", "import_from_statement", "import_declaration",
                    "use_declaration", "preproc_include"}

    def walk(node, enclosing: str):
        nonlocal symbols, edges
        t = node.type
        owner = enclosing
        if t in func_types:
            nm = name_child(node) or "<anon>"
            kind = "method" if enclosing != "<module>" else "function"
            symbols.append(Symbol(nm, kind, rel, node.start_point[0] + 1,
                                  node.end_point[0] + 1, first_line(node)))
            owner = nm
        elif t in class_types:
            nm = name_child(node) or "<anon>"
            symbols.append(Symbol(nm, "class", rel, node.start_point[0] + 1,
                                  node.end_point[0] + 1, first_line(node)))
            owner = nm
        elif t in import_types:
            edges.append(Edge("<module>", first_line(node)[:80], "imports", rel))
        elif t in call_types:
            fn = node.child_by_field_name("function")
            target = _node_text(src, fn).split("(")[0].strip() if fn is not None else ""
            if target:
                edges.append(Edge(enclosing, target.split(".")[-1], "calls", rel))
        for child in node.children:
            walk(child, owner)

    walk(tree.root_node, "<module>")
    return symbols, edges


# --- regex fallback (best-effort, language-agnostic-ish) --------------------

_PY_CLASS = re.compile(r"^(\s*)class\s+(\w+)")
_PY_DEF = re.compile(r"^(\s*)def\s+(\w+)\s*\(")
_PY_IMPORT = re.compile(r"^\s*(?:from\s+[\w.]+\s+import\s+.+|import\s+.+)$")
_PY_CALL = re.compile(r"(\w+)\s*\(")
_PY_TOPVAR = re.compile(r"^(\w+)\s*[:=]")

_JS_FUNC = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(")
_JS_CLASS = re.compile(r"^\s*(?:export\s+)?class\s+(\w+)")
_JS_CONST_FN = re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(")
_JS_IMPORT = re.compile(r"^\s*import\s+.+")


def _parse_regex(path: Path, text: str):
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    rel = str(path)
    lines = text.splitlines()
    is_py = path.suffix.lower() == ".py"
    enclosing = "<module>"
    enclosing_indent = -1

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        indent = len(line) - len(line.lstrip())

        if is_py:
            if (m := _PY_CLASS.match(line)):
                enclosing, enclosing_indent = m.group(2), indent
                symbols.append(Symbol(m.group(2), "class", rel, i, i, line.strip()))
                continue
            if (m := _PY_DEF.match(line)):
                kind = "method" if indent > 0 else "function"
                if indent <= enclosing_indent:
                    enclosing = "<module>"
                symbols.append(Symbol(m.group(2), kind, rel, i, i, line.strip()))
                continue
            if _PY_IMPORT.match(line):
                edges.append(Edge("<module>", line.strip()[:80], "imports", rel))
                continue
            if indent == 0 and (m := _PY_TOPVAR.match(line)) and not line.startswith(("def", "class")):
                symbols.append(Symbol(m.group(1), "variable", rel, i, i, line.strip()[:80]))
            for cm in _PY_CALL.finditer(line):
                callee = cm.group(1)
                if callee not in {"if", "for", "while", "print", "len", "range"}:
                    edges.append(Edge(enclosing, callee, "calls", rel))
        else:
            if (m := _JS_CLASS.match(line)):
                symbols.append(Symbol(m.group(1), "class", rel, i, i, line.strip()))
            elif (m := _JS_FUNC.match(line)):
                symbols.append(Symbol(m.group(1), "function", rel, i, i, line.strip()))
            elif (m := _JS_CONST_FN.match(line)):
                symbols.append(Symbol(m.group(1), "function", rel, i, i, line.strip()))
            elif _JS_IMPORT.match(line):
                edges.append(Edge("<module>", line.strip()[:80], "imports", rel))

    if is_py:
        _compute_py_spans(symbols, lines)
    return symbols, edges


def _compute_py_spans(symbols: list[Symbol], lines: list[str]) -> None:
    """Set ``end_line`` for class/function/method symbols by indentation.

    The regex pass records only the def line; here we extend each block to its
    last indented (or blank) line so ``explain`` can pull the real body.
    """
    blocks = [s for s in symbols if s.kind in {"class", "function", "method"}]
    for s in blocks:
        if s.start_line - 1 >= len(lines):
            continue
        header = lines[s.start_line - 1]
        base_indent = len(header) - len(header.lstrip())
        end = s.start_line
        for j in range(s.start_line, len(lines)):
            ln = lines[j]
            if ln.strip() == "":
                end = j + 1  # tentatively include trailing blanks
                continue
            indent = len(ln) - len(ln.lstrip())
            if indent <= base_indent:
                break
            end = j + 1
        s.end_line = end


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    lang TEXT,
    indexed_at REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    file TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    signature TEXT,
    docstring TEXT,
    FOREIGN KEY(file) REFERENCES files(path) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    kind TEXT NOT NULL,
    file TEXT NOT NULL,
    FOREIGN KEY(file) REFERENCES files(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
"""


class CodeIndex:
    """SQLite-backed symbol + reference store with content-hash caching."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- ingestion ----------------------------------------------------------

    def _stored_hash(self, path: str) -> str | None:
        row = self.conn.execute("SELECT hash FROM files WHERE path = ?", (path,)).fetchone()
        return row["hash"] if row else None

    def index_file(self, path: Path, *, force: bool = False) -> bool:
        """Re-parse *path* if its content hash changed. Returns True if parsed."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        key = str(path)
        h = file_hash(text)
        if not force and self._stored_hash(key) == h:
            return False  # cache hit — unchanged

        symbols, edges = parse_symbols(path, text)
        cur = self.conn
        cur.execute("DELETE FROM symbols WHERE file = ?", (key,))
        cur.execute("DELETE FROM edges WHERE file = ?", (key,))
        cur.execute(
            "INSERT INTO files(path, hash, lang) VALUES(?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET hash=excluded.hash, lang=excluded.lang, "
            "indexed_at=strftime('%s','now')",
            (key, h, LANG_BY_EXT.get(path.suffix.lower())),
        )
        cur.executemany(
            "INSERT INTO symbols(name,kind,file,start_line,end_line,signature,docstring) "
            "VALUES(?,?,?,?,?,?,?)",
            [(s.name, s.kind, s.file, s.start_line, s.end_line, s.signature, s.docstring)
             for s in symbols],
        )
        cur.executemany(
            "INSERT INTO edges(src,dst,kind,file) VALUES(?,?,?,?)",
            [(e.src, e.dst, e.kind, e.file) for e in edges],
        )
        cur.commit()
        return True

    def index_tree(self, root: Path) -> dict[str, int]:
        """Walk *root*, index supported files. Returns counts."""
        parsed = skipped = 0
        present: set[str] = set()
        for path in iter_source_files(root):
            present.add(str(path))
            if self.index_file(path):
                parsed += 1
            else:
                skipped += 1
        # prune deleted files
        rows = self.conn.execute("SELECT path FROM files").fetchall()
        removed = 0
        for r in rows:
            if r["path"] not in present and Path(r["path"]).is_absolute():
                if not Path(r["path"]).exists():
                    self.conn.execute("DELETE FROM files WHERE path = ?", (r["path"],))
                    self.conn.execute("DELETE FROM symbols WHERE file = ?", (r["path"],))
                    self.conn.execute("DELETE FROM edges WHERE file = ?", (r["path"],))
                    removed += 1
        self.conn.commit()
        return {"parsed": parsed, "skipped": skipped, "removed": removed,
                "symbols": self.symbol_count()}

    # -- queries ------------------------------------------------------------

    def symbol_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM symbols").fetchone()["c"]

    def all_symbols(self) -> list[Symbol]:
        rows = self.conn.execute(
            "SELECT name,kind,file,start_line,end_line,signature,docstring FROM symbols"
        ).fetchall()
        return [Symbol(**dict(r)) for r in rows]

    def all_edges(self) -> list[Edge]:
        rows = self.conn.execute("SELECT src,dst,kind,file FROM edges").fetchall()
        return [Edge(**dict(r)) for r in rows]

    def find_symbol(self, name: str) -> list[Symbol]:
        rows = self.conn.execute(
            "SELECT name,kind,file,start_line,end_line,signature,docstring "
            "FROM symbols WHERE name = ? ORDER BY kind", (name,)
        ).fetchall()
        return [Symbol(**dict(r)) for r in rows]

    def files(self) -> list[str]:
        return [r["path"] for r in self.conn.execute("SELECT path FROM files").fetchall()]


def iter_source_files(root: Path) -> Iterator[Path]:
    root = Path(root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in LANG_BY_EXT:
            yield path
