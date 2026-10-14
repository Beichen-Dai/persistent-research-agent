"""A folder of PDF files (e.g. Zotero's ``storage`` directory) with full-text search.

Configuration::

    [[sources]]
    name = "papers"
    type = "pdf_folder"
    path = "~/Zotero/storage"          # searched recursively for *.pdf
    description = "My reference library"

Run ``/index papers`` once (and again after adding PDFs); indexing is
incremental, so unchanged files are skipped.
"""

from __future__ import annotations

import re
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
    path TEXT UNIQUE NOT NULL,
    title TEXT,
    filename TEXT,
    pages INTEGER,
    file_size INTEGER,
    file_mtime REAL,
    sha256 TEXT,
    indexed_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
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

CHUNK_CHARS = 1800


def clean_pdf_text(text: str) -> str:
    text = (text or "").replace("\x00", " ")
    text = re.sub(r"-\s*\n\s*", "", text)  # re-join words hyphenated at line ends
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split page text into chunks at sentence boundaries."""
    text = clean_pdf_text(text)
    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= max_chars:
            current = f"{current} {sentence}" if current else sentence
            continue
        if current:
            chunks.append(current)
        current = ""
        while len(sentence) > max_chars:
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        current = sentence
    if current:
        chunks.append(current)
    return chunks


def pdf_title(reader: Any, path: Path) -> str:
    """Title from the PDF metadata if it looks real, otherwise the file name."""
    try:
        title = str((reader.metadata or {}).get("/Title") or "").strip()
    except Exception:
        title = ""
    return title if len(title) > 5 else path.stem


class PdfFolderSource(DataSource):
    """Recursively indexed folder of PDF files."""

    type_name = "pdf_folder"
    evidence_label = "PAPER"
    filters_help = (
        '{"mode": "title"} searches file names and titles instead of the full text '
        "(works before indexing)."
    )

    def __init__(self, name: str, data_dir: Path, description: str = "", **options: Any) -> None:
        super().__init__(name, data_dir, description or "Folder of PDF documents", **options)
        if "path" not in options:
            raise SourceError(f"Source '{name}': the option 'path' is required.")
        self.root = Path(options["path"]).expanduser()
        self.db_path = self.data_dir / "index.db"

    def _db(self):
        return fts.connect(self.db_path, SCHEMA)

    def _pdfs(self) -> list[Path]:
        if not self.root.is_dir():
            raise SourceError(f"PDF folder not found: {self.root}")
        return sorted(self.root.rglob("*.pdf"))

    # -- indexing -----------------------------------------------------------

    def index(self, force: bool = False) -> dict[str, Any]:
        from pypdf import PdfReader

        pdfs = self._pdfs()
        summary = {"found": len(pdfs), "indexed": 0, "unchanged": 0, "removed": 0, "errors": []}
        conn = self._db()
        try:
            known = {
                row["path"]: row for row in conn.execute("SELECT id, path, sha256 FROM documents")
            }
            for i, pdf in enumerate(pdfs, 1):
                key = str(pdf.resolve())
                try:
                    sha = fts.file_sha256(pdf)
                    if key in known and known[key]["sha256"] == sha and not force:
                        summary["unchanged"] += 1
                        continue
                    print(f"[{self.name}] {i}/{len(pdfs)} indexing {pdf.name}")
                    reader = PdfReader(str(pdf))
                    if key in known:
                        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (known[key]["id"],))
                        conn.execute("DELETE FROM documents WHERE id = ?", (known[key]["id"],))
                    stat = pdf.stat()
                    doc_id = conn.execute(
                        "INSERT INTO documents (path, title, filename, pages, file_size, "
                        "file_mtime, sha256, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            key,
                            pdf_title(reader, pdf),
                            pdf.name,
                            len(reader.pages),
                            stat.st_size,
                            stat.st_mtime,
                            sha,
                            now_iso(),
                        ),
                    ).lastrowid
                    for page_no, page in enumerate(reader.pages, 1):
                        try:
                            text = page.extract_text() or ""
                        except Exception:
                            text = ""
                        for chunk_no, chunk in enumerate(split_sentences(text)):
                            conn.execute(
                                "INSERT INTO chunks (doc_id, page, chunk_index, text) "
                                "VALUES (?, ?, ?, ?)",
                                (doc_id, page_no, chunk_no, chunk),
                            )
                    conn.commit()
                    summary["indexed"] += 1
                except Exception as exc:  # one broken PDF must not stop the run
                    conn.rollback()
                    summary["errors"].append({"path": key, "error": f"{type(exc).__name__}: {exc}"})

            present = {str(p.resolve()) for p in pdfs}
            for path, row in known.items():
                if path not in present:
                    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (row["id"],))
                    conn.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
                    summary["removed"] += 1
            conn.commit()
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('optimize')")
            conn.commit()
        finally:
            conn.close()
        return summary

    # -- search -------------------------------------------------------------

    def search(
        self, query: str, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[SearchHit]:
        if (filters or {}).get("mode") == "title":
            return self._search_titles(query, limit)
        if not fts.tokens(query):
            return []

        sql = """
            SELECT d.id, d.title, d.path, c.page, c.text, bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON chunks_fts.rowid = c.id
            JOIN documents d ON c.doc_id = d.id
            WHERE chunks_fts MATCH ?
            ORDER BY score LIMIT ?
        """
        conn = self._db()
        try:
            rows = []
            # All terms first (precise); if nothing matches, any term ranked by
            # BM25 (natural-language questions).
            for joiner in ("AND", "OR"):
                rows = conn.execute(sql, (fts.match_expression(query, joiner), limit)).fetchall()
                if rows:
                    break
        finally:
            conn.close()
        return [
            SearchHit(
                doc_id=str(r["id"]),
                title=r["title"],
                text=r["text"],
                location=f"p. {r['page']}",
                score=round(r["score"], 3),
                metadata={"path": r["path"]},
            )
            for r in rows
        ]

    def _search_titles(self, query: str, limit: int) -> list[SearchHit]:
        words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 1]
        if not words:
            return []
        conn = self._db()
        try:
            indexed = {r["path"]: r for r in conn.execute("SELECT id, path, title FROM documents")}
        finally:
            conn.close()

        scored = []
        for pdf in self._pdfs():
            key = str(pdf.resolve())
            row = indexed.get(key)
            title = row["title"] if row else pdf.stem
            haystack = f"{title} {pdf.stem}".lower()
            score = sum(w in haystack for w in words)
            if score:
                doc_id = str(row["id"]) if row else key
                scored.append(
                    (
                        score,
                        SearchHit(
                            doc_id=doc_id, title=title, text="", score=score, metadata={"path": key}
                        ),
                    )
                )
        scored.sort(key=lambda item: -item[0])
        return [hit for _, hit in scored[:limit]]

    # -- documents ----------------------------------------------------------

    def _resolve_path(self, doc_id: str) -> Path:
        """``doc_id`` is an index id or a PDF path inside the folder."""
        if doc_id.isdigit():
            conn = self._db()
            try:
                row = conn.execute(
                    "SELECT path FROM documents WHERE id = ?", (int(doc_id),)
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                raise SourceError(f"Unknown document id: {doc_id}")
            return Path(row["path"])
        path = Path(doc_id).expanduser().resolve()
        if self.root.resolve() not in path.parents or path.suffix.lower() != ".pdf":
            raise SourceError(f"Not a PDF inside {self.root}: {doc_id}")
        return path

    def read(self, doc_id: str, max_chars: int = 30_000) -> Document:
        from pypdf import PdfReader

        path = self._resolve_path(doc_id)
        if not path.exists():
            raise SourceError(f"File not found: {path}")
        reader = PdfReader(str(path))
        parts, total, truncated = [], 0, False
        for page_no, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            block = f"[p.{page_no}]\n{text}"
            if total + len(block) > max_chars:
                parts.append(block[: max_chars - total])
                truncated = True
                break
            parts.append(block)
            total += len(block)
        return Document(
            doc_id=doc_id,
            title=pdf_title(reader, path),
            text="\n\n".join(parts),
            metadata={"path": str(path), "pages": len(reader.pages)},
            truncated=truncated,
        )

    def list_documents(self, limit: int = 50, offset: int = 0) -> list[DocumentInfo]:
        conn = self._db()
        try:
            rows = conn.execute(
                "SELECT id, title, pages, path FROM documents ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        finally:
            conn.close()
        return [
            DocumentInfo(str(r["id"]), r["title"], {"pages": r["pages"], "path": r["path"]})
            for r in rows
        ]

    def status(self) -> dict[str, Any]:
        conn = self._db()
        try:
            docs, pages = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(pages), 0) FROM documents"
            ).fetchone()
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
        return {"folder": str(self.root), "documents": docs, "pages": pages, "chunks": chunks}

    def instructions(self) -> str:
        return (
            f"Source '{self.name}' holds PDF documents. Cite them as [{self.evidence_label}] "
            "with the paper name and page. Use read_document for details of one paper. "
            "Never infer results from file names or titles."
        )

    # -- knowledge cards ----------------------------------------------------

    def card_candidates(self) -> Iterator[CardCandidate]:
        conn = self._db()
        try:
            rows = conn.execute("SELECT id, title, sha256 FROM documents ORDER BY id").fetchall()
        finally:
            conn.close()
        for r in rows:
            yield CardCandidate(str(r["id"]), r["title"], r["sha256"])

    def card_text(self, doc_id: str, max_chars: int) -> str:
        conn = self._db()
        try:
            rows = conn.execute(
                "SELECT page, text FROM chunks WHERE doc_id = ? ORDER BY page, chunk_index",
                (int(doc_id),),
            ).fetchall()
        finally:
            conn.close()
        parts, total, last_page = [], 0, None
        for r in rows:
            text = r["text"]
            if r["page"] != last_page:
                text = f"\n[p.{r['page']}] {text}"
                last_page = r["page"]
            if total + len(text) > max_chars:
                parts.append(text[: max_chars - total])
                break
            parts.append(text)
            total += len(text)
        return " ".join(parts)
