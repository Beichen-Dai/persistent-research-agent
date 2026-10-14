"""Small helpers shared by sources that use SQLite FTS5 full-text search."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

# Latin words/identifiers, or runs of CJK characters.
_TOKEN = re.compile(r"[A-Za-z0-9_.:*-]+|[\u4e00-\u9fff]+")


def connect(path: Path, schema: str) -> sqlite3.Connection:
    """Open (and create if needed) a SQLite database with the given schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(schema)
    return conn


def tokens(query: str) -> list[str]:
    """Split a free-text query into FTS5-safe tokens."""
    out = []
    for token in _TOKEN.findall(query):
        token = re.sub(r"[\"'()]", "", token).strip(".:-")
        if token:
            out.append(token)
    return out


def match_expression(query: str, joiner: str = "OR") -> str:
    """Build an FTS5 MATCH expression joining quoted tokens with AND/OR."""
    return f" {joiner} ".join(f'"{t}"' for t in tokens(query))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def split_paragraphs(text: str, max_chars: int) -> list[str]:
    """Split text into chunks of at most ``max_chars``, at blank lines if possible."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > max_chars:  # a single oversized paragraph
            if current:
                chunks.append("\n\n".join(current))
                current, size = [], 0
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        extra = len(paragraph) + (2 if current else 0)
        if current and size + extra > max_chars:
            chunks.append("\n\n".join(current))
            current, size = [], 0
            extra = len(paragraph)
        current.append(paragraph)
        size += extra
    if current:
        chunks.append("\n\n".join(current))
    return chunks
