"""A folder of plain-text files (Markdown notes, logs, code, ...) with full-text search.

Configuration::

    [[sources]]
    name = "notes"
    type = "text_folder"
    path = "~/notes"
    patterns = ["*.md", "*.txt"]        # optional, default shown
    description = "My lab notebook"

Run ``/index notes`` after adding or editing files.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from research_agent.sources import fts
from research_agent.sources.base import (
    CardCandidate,
    DataSource,
    Document,
    DocumentInfo,
    SearchHit,
    SourceError,
)
from research_agent.store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path TEXT UNIQUE NOT NULL,
    title TEXT,
    sha256 TEXT,
    indexed_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
    USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""

DEFAULT_PATTERNS = ["*.md", "*.txt"]
CHUNK_CHARS = 3000
MAX_FILE_BYTES = 5_000_000


def first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines()[:20]:
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or fallback
    return fallback


class TextFolderSource(DataSource):
    """Recursively indexed folder of text files."""

    type_name = "text_folder"
    evidence_label = "NOTE"

    def __init__(self, name: str, data_dir: Path, description: str = "", **options: Any) -> None:
        super().__init__(name, data_dir, description or "Folder of text notes", **options)
        if "path" not in options:
            raise SourceError(f"Source '{name}': the option 'path' is required.")
        self.root = Path(options["path"]).expanduser()
        self.patterns = list(options.get("patterns", DEFAULT_PATTERNS))
        self.db_path = self.data_dir / "index.db"

    def _db(self):
        return fts.connect(self.db_path, SCHEMA)

    def _files(self) -> list[Path]:
        if not self.root.is_dir():
            raise SourceError(f"Folder not found: {self.root}")
        files = {p for pattern in self.patterns for p in self.root.rglob(pattern) if p.is_file()}
        return sorted(p for p in files if p.stat().st_size <= MAX_FILE_BYTES)

    def index(self, force: bool = False) -> dict[str, Any]:
        files = self._files()
        summary = {"found": len(files), "indexed": 0, "unchanged": 0, "removed": 0}
        conn = self._db()
        try:
            known = {
                r["rel_path"]: r for r in conn.execute("SELECT id, rel_path, sha256 FROM documents")
            }
            for path in files:
                rel = str(path.relative_to(self.root))
                sha = fts.file_sha256(path)
                if rel in known and known[rel]["sha256"] == sha and not force:
                    summary["unchanged"] += 1
                    continue
                if rel in known:
                    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (known[rel]["id"],))
                    conn.execute("DELETE FROM documents WHERE id = ?", (known[rel]["id"],))
                text = path.read_text(encoding="utf-8", errors="replace")
                doc_id = conn.execute(
                    "INSERT INTO documents (rel_path, title, sha256, indexed_at) "
                    "VALUES (?, ?, ?, ?)",
                    (rel, first_heading(text, path.stem), sha, now_iso()),
                ).lastrowid
                for i, chunk in enumerate(fts.split_paragraphs(text, CHUNK_CHARS)):
                    conn.execute(
                        "INSERT INTO chunks (doc_id, chunk_index, text) VALUES (?, ?, ?)",
                        (doc_id, i, chunk),
                    )
                summary["indexed"] += 1

            present = {str(p.relative_to(self.root)) for p in files}
            for rel, row in known.items():
                if rel not in present:
                    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (row["id"],))
                    conn.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
                    summary["removed"] += 1
            conn.commit()
        finally:
            conn.close()
        return summary

    def search(
        self, query: str, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[SearchHit]:
        expression = fts.match_expression(query, "OR")
        if not expression:
            return []
        conn = self._db()
        try:
            rows = conn.execute(
                """
                SELECT d.id, d.title, d.rel_path, c.chunk_index, c.text, bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.id
                JOIN documents d ON c.doc_id = d.id
                WHERE chunks_fts MATCH ?
                ORDER BY score LIMIT ?
                """,
                (expression, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            SearchHit(
                doc_id=str(r["id"]),
                title=r["title"],
                text=r["text"],
                location=f"{r['rel_path']} (part {r['chunk_index'] + 1})",
                score=round(r["score"], 3),
            )
            for r in rows
        ]

    def _row(self, doc_id: str):
        conn = self._db()
        try:
            row = conn.execute(
                "SELECT id, rel_path, title, sha256 FROM documents WHERE id = ?", (int(doc_id),)
            ).fetchone()
        except ValueError:
            row = None
        finally:
            conn.close()
        if row is None:
            raise SourceError(f"Unknown document id: {doc_id}")
        return row

    def read(self, doc_id: str, max_chars: int = 30_000) -> Document:
        row = self._row(doc_id)
        text = (self.root / row["rel_path"]).read_text(encoding="utf-8", errors="replace")
        return Document(
            doc_id=doc_id,
            title=row["title"],
            text=text[:max_chars],
            metadata={"path": row["rel_path"]},
            truncated=len(text) > max_chars,
        )

    def list_documents(self, limit: int = 50, offset: int = 0) -> list[DocumentInfo]:
        conn = self._db()
        try:
            rows = conn.execute(
                "SELECT id, title, rel_path FROM documents ORDER BY rel_path LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        finally:
            conn.close()
        return [DocumentInfo(str(r["id"]), r["title"], {"path": r["rel_path"]}) for r in rows]

    def status(self) -> dict[str, Any]:
        conn = self._db()
        try:
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
        return {
            "folder": str(self.root),
            "patterns": self.patterns,
            "documents": docs,
            "chunks": chunks,
        }

    def card_candidates(self) -> Iterator[CardCandidate]:
        if not self.options.get("cards", False):
            return
        conn = self._db()
        try:
            rows = conn.execute("SELECT id, title, sha256 FROM documents ORDER BY id").fetchall()
        finally:
            conn.close()
        for r in rows:
            yield CardCandidate(str(r["id"]), r["title"], r["sha256"])

    def card_text(self, doc_id: str, max_chars: int) -> str:
        return self.read(doc_id, max_chars).text
