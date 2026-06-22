"""Document pipeline (§3.3).

Convert PDF / Word / Excel attachments to clean Markdown locally with
``markitdown`` before they reach the model, and hash-cache the result so the
same file is never converted twice. With ``markitdown`` absent we fall back to
plain-text passthrough for text-like files and a clear stub otherwise.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CONVERTIBLE = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".html", ".htm"}
TEXTLIKE = {".txt", ".md", ".markdown", ".csv", ".json", ".rst"}


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DocPipeline:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, digest: str) -> Path:
        return self.cache_dir / f"doc_{digest}.md"

    def convert(self, path: Path) -> str:
        """Return Markdown for *path*, using the hash cache when warm."""
        path = Path(path)
        data = path.read_bytes()
        digest = _hash_bytes(data)
        cached = self._cache_path(digest)
        if cached.exists():
            return cached.read_text(encoding="utf-8")

        md = self._convert_uncached(path, data)
        cached.write_text(md, encoding="utf-8")
        return md

    def _convert_uncached(self, path: Path, data: bytes) -> str:
        ext = path.suffix.lower()
        try:
            from markitdown import MarkItDown

            result = MarkItDown().convert(str(path))
            return result.text_content
        except ImportError:
            pass
        except Exception as exc:  # pragma: no cover
            return f"<!-- markitdown failed for {path.name}: {exc} -->"

        if ext in TEXTLIKE:
            return data.decode("utf-8", "replace")
        return (f"<!-- {path.name}: install `markitdown` to convert {ext} files "
                f"to Markdown (cached after first run) -->")

    def is_convertible(self, path: Path) -> bool:
        ext = Path(path).suffix.lower()
        return ext in CONVERTIBLE or ext in TEXTLIKE
