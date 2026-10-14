"""The official LAMMPS documentation and example inputs, from a LAMMPS source tree.

Configuration::

    [[sources]]
    name = "lammps"
    type = "lammps_docs"
    path = "~/src/lammps-22Jul2025"     # a LAMMPS source tree (doc/src, examples)
    script_dirs = ["workspace"]          # optional: your input scripts, for the cheat sheet

Run ``/index lammps`` once. ``/source lammps cheatsheet`` builds a short
cheat sheet (verbatim syntax and examples of the commands you use) that is
sent with every request.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from research_agent.sources import fts
from research_agent.sources.base import DataSource, Document, DocumentInfo, SearchHit, SourceError
from research_agent.store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    rel_path TEXT NOT NULL,
    title TEXT,
    category TEXT,
    subcategory TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
    USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""

# Commands always in the cheat sheet, besides those found in the user's scripts.
CORE_COMMANDS = [
    "units",
    "atom_style",
    "boundary",
    "read_data",
    "create_box",
    "create_atoms",
    "lattice",
    "region",
    "mass",
    "pair_style",
    "pair_coeff",
    "kspace_style",
    "neighbor",
    "neigh_modify",
    "velocity",
    "timestep",
    "thermo",
    "thermo_style",
    "dump",
    "minimize",
    "run",
    "fix nvt",
    "fix npt",
    "fix nve",
    "compute rdf",
    "compute msd",
    "write_data",
    "read_restart",
    "write_restart",
]

# Commands recorded as metadata of example inputs.
TRACKED_COMMANDS = {
    "atom_style",
    "boundary",
    "bond_style",
    "angle_style",
    "dihedral_style",
    "improper_style",
    "pair_style",
    "pair_coeff",
    "kspace_style",
    "neighbor",
    "neigh_modify",
    "timestep",
    "thermo",
    "thermo_style",
    "velocity",
    "minimize",
    "run",
    "dump",
    "fix",
    "compute",
    "region",
    "create_box",
    "create_atoms",
    "read_data",
    "read_restart",
    "write_data",
    "write_restart",
    "molecule",
    "include",
    "variable",
    "package",
    "suffix",
}

INSTRUCTIONS = """\
Source '{name}' contains the official LAMMPS {version} documentation ([DOC]) and
official example inputs ([EXAMPLE]); search it with filters {{"doc_type": "doc"}} or
{{"doc_type": "example"}}.
Do NOT rely on model memory for LAMMPS syntax. For a LAMMPS task:
1. search the documentation for exact syntax, arguments, restrictions and defaults;
2. search the examples when a practical input pattern helps;
3. write the input based on what was retrieved;
4. if asked to run it, run the local LAMMPS and inspect the actual log and output ([LOCAL]).
The installed LAMMPS version is authoritative; do not assume syntax from other releases."""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip()


def rst_title(text: str, fallback: str) -> str:
    lines = text.splitlines()
    for line, underline in zip(lines, lines[1:], strict=False):
        line, underline = line.strip(), underline.strip()
        if line and len(underline) >= 3 and set(underline) <= set('=-~^"') and len(line) <= 200:
            return line
    return fallback


def script_commands(text: str) -> dict[str, list[str]]:
    """Commands and pair/fix/compute styles used in a LAMMPS input script."""
    found: dict[str, set[str]] = {
        "commands": set(),
        "pair_styles": set(),
        "fix_styles": set(),
        "compute_styles": set(),
    }
    for raw in text.splitlines():
        words = raw.split("#", 1)[0].split()
        if not words:
            continue
        cmd = words[0]
        if cmd in TRACKED_COMMANDS:
            found["commands"].add(cmd)
        if cmd == "pair_style" and len(words) > 1:
            found["pair_styles"].add(words[1])
        elif cmd == "fix" and len(words) > 3:
            found["fix_styles"].add(words[3])
        elif cmd == "compute" and len(words) > 3:
            found["compute_styles"].add(words[3])
    return {key: sorted(values) for key, values in found.items()}


def rst_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^{heading}\n[\"=~^-]+\n(.*?)(?=^\S[^\n]*\n[\"=~^-]{{3,}}\n|\Z)", text, flags=re.M | re.S
    )
    return match.group(1).strip() if match else ""


def compact(text: str, max_lines: int) -> str:
    lines = [
        ln.rstrip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(".. ")
    ]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["   ..."]
    return "\n".join(lines)


class LammpsDocsSource(DataSource):
    """Full-text search over LAMMPS ``doc/src/*.rst`` and ``examples/**/in.*``."""

    type_name = "lammps_docs"
    evidence_label = "DOC"
    filters_help = (
        '{"doc_type": "doc" | "example"} and/or {"category": "<example category>", '
        'e.g. "PACKAGES" or "KOKKOS"}'
    )

    def __init__(self, name: str, data_dir: Path, description: str = "", **options: Any) -> None:
        if "path" not in options:
            raise SourceError(
                f"Source '{name}': the option 'path' (LAMMPS source tree) is required."
            )
        self.root = Path(options["path"]).expanduser()
        self.version = options.get("version") or self.root.name
        super().__init__(
            name,
            data_dir,
            description or f"Official LAMMPS documentation and examples ({self.version})",
            **options,
        )
        self.script_dirs = [Path(p).expanduser() for p in options.get("script_dirs", [])]
        self.db_path = self.data_dir / "index.db"
        self.cheatsheet_path = self.data_dir / "cheatsheet.md"

    def _db(self):
        return fts.connect(self.db_path, SCHEMA)

    # -- indexing -----------------------------------------------------------

    def index(self, force: bool = False) -> dict[str, Any]:
        for sub in ("doc/src", "examples"):
            if not (self.root / sub).is_dir():
                raise SourceError(f"Not a LAMMPS source tree (missing {sub}): {self.root}")

        conn = self._db()
        try:
            # A full rebuild is fast enough and avoids synchronisation bugs.
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            docs = self._index_docs(conn)
            examples = self._index_examples(conn)
            conn.commit()
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('optimize')")
            conn.commit()
        finally:
            conn.close()
        return {"root": str(self.root), "docs": docs, "examples": examples, "built": now_iso()}

    def _add(
        self,
        conn,
        doc_type: str,
        path: Path,
        title: str,
        text: str,
        max_chars: int,
        category: str | None = None,
        subcategory: str | None = None,
    ) -> None:
        doc_id = conn.execute(
            "INSERT INTO documents (doc_type, path, rel_path, title, category, subcategory) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (doc_type, str(path), str(path.relative_to(self.root)), title, category, subcategory),
        ).lastrowid
        for i, chunk in enumerate(fts.split_paragraphs(text, max_chars)):
            conn.execute(
                "INSERT INTO chunks (document_id, chunk_index, text) VALUES (?, ?, ?)",
                (doc_id, i, chunk),
            )

    def _index_docs(self, conn) -> int:
        count = 0
        for path in sorted((self.root / "doc" / "src").rglob("*.rst")):
            text = read_text(path)
            if text:
                self._add(conn, "doc", path, rst_title(text, path.stem), text, 12_000)
                count += 1
        return count

    def _index_examples(self, conn) -> int:
        count = 0
        examples = self.root / "examples"
        for path in sorted(examples.rglob("in.*")):
            text = read_text(path)
            if not text:
                continue
            parts = path.relative_to(examples).parts
            category = parts[0] if len(parts) > 1 else None
            subcategory = parts[1] if len(parts) > 2 else None
            title = "/".join(p for p in (category, subcategory, path.name) if p)
            readme = "\n\n".join(
                read_text(p)[:8000]
                for p in sorted(path.parent.iterdir())
                if p.is_file() and p.name.lower().startswith("readme")
            )
            meta = script_commands(text)
            # Metadata lines make searches such as "pair_style pace" effective.
            header = "\n".join(
                [
                    "LAMMPS EXAMPLE",
                    f"path: {path.relative_to(self.root)}",
                    f"category: {category or ''}",
                    f"commands: {' '.join(meta['commands'])}",
                    f"pair_styles: {' '.join(meta['pair_styles'])}",
                    f"fix_styles: {' '.join(meta['fix_styles'])}",
                    f"compute_styles: {' '.join(meta['compute_styles'])}",
                ]
            )
            body = f"{header}\n\n{text}" + (f"\n\nREADME DESCRIPTION\n{readme}" if readme else "")
            self._add(conn, "example", path, title, body, 30_000, category, subcategory)
            count += 1
        return count

    # -- search and read ----------------------------------------------------

    def search(
        self, query: str, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[SearchHit]:
        expression = fts.match_expression(query, "OR")
        if not expression:
            return []
        filters = filters or {}
        where, params = [], [expression]
        for key in ("doc_type", "category"):
            if filters.get(key):
                where.append(f"d.{key} = ?")
                params.append(filters[key])
        extra = "".join(f" AND {w}" for w in where)
        conn = self._db()
        try:
            rows = conn.execute(
                f"""
                SELECT d.id, d.doc_type, d.rel_path, d.title, d.category, c.chunk_index, c.text,
                       bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.id
                JOIN documents d ON c.document_id = d.id
                WHERE chunks_fts MATCH ?{extra}
                ORDER BY score LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            SearchHit(
                doc_id=str(r["id"]),
                title=r["title"],
                text=r["text"],
                location=r["rel_path"],
                score=round(r["score"], 3),
                metadata={
                    "doc_type": r["doc_type"],
                    "category": r["category"],
                    "label": "DOC" if r["doc_type"] == "doc" else "EXAMPLE",
                },
            )
            for r in rows
        ]

    def read(self, doc_id: str, max_chars: int = 30_000) -> Document:
        """Return the indexed text: the full rst page, or an example input with its README."""
        conn = self._db()
        try:
            row = conn.execute(
                "SELECT rel_path, title, doc_type FROM documents WHERE id = ?",
                (int(doc_id) if doc_id.isdigit() else -1,),
            ).fetchone()
            chunks = conn.execute(
                "SELECT text FROM chunks WHERE document_id = ? ORDER BY chunk_index",
                (int(doc_id) if doc_id.isdigit() else -1,),
            ).fetchall()
        finally:
            conn.close()
        if row is None:
            raise SourceError(f"Unknown document id: {doc_id}")
        text = "\n\n".join(c["text"] for c in chunks)
        return Document(
            doc_id=doc_id,
            title=row["title"],
            text=text[:max_chars],
            metadata={"path": row["rel_path"], "doc_type": row["doc_type"]},
            truncated=len(text) > max_chars,
        )

    def list_documents(self, limit: int = 50, offset: int = 0) -> list[DocumentInfo]:
        conn = self._db()
        try:
            rows = conn.execute(
                "SELECT id, title, rel_path, doc_type FROM documents ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        finally:
            conn.close()
        return [
            DocumentInfo(
                str(r["id"]), r["title"], {"path": r["rel_path"], "doc_type": r["doc_type"]}
            )
            for r in rows
        ]

    def status(self) -> dict[str, Any]:
        conn = self._db()
        try:
            counts = dict(
                conn.execute("SELECT doc_type, COUNT(*) FROM documents GROUP BY doc_type")
            )
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
        return {
            "root": str(self.root),
            "docs": counts.get("doc", 0),
            "examples": counts.get("example", 0),
            "chunks": chunks,
            "cheatsheet": str(self.cheatsheet_path) if self.cheatsheet_path.exists() else None,
        }

    def instructions(self) -> str:
        return INSTRUCTIONS.format(name=self.name, version=self.version)

    def resident_context(self) -> str | None:
        if not self.cheatsheet_path.exists():
            return None
        return (
            f"=== LAMMPS CHEAT SHEET (verbatim from the local {self.version} docs = [DOC]) ===\n"
            + self.cheatsheet_path.read_text(encoding="utf-8", errors="replace")
        )

    def actions(self) -> dict[str, tuple[Any, str]]:
        return {
            "cheatsheet": (
                self.build_cheatsheet,
                "Build the cheat sheet from the docs and the commands in your input scripts.",
            )
        }

    # -- cheat sheet --------------------------------------------------------

    def _user_commands(self) -> Counter:
        found: Counter = Counter()
        for root in self.script_dirs:
            if not root.is_dir():
                continue
            for pattern in ("in.*", "*.in", "*.lmp"):
                for path in root.rglob(pattern):
                    if not path.is_file() or path.stat().st_size > 2_000_000:
                        continue
                    for line in read_text(path).splitlines():
                        words = line.split("#", 1)[0].split()
                        if not words:
                            continue
                        cmd = words[0]
                        if cmd in ("fix", "compute") and len(words) > 3:
                            found[f"{cmd} {words[3]}"] += 1
                        elif cmd.endswith("_style") and len(words) > 1:
                            found[f"{cmd} {words[1]}"] += 1
                        else:
                            found[cmd] += 1
        return found

    def build_cheatsheet(self, max_commands: int = 40) -> dict[str, Any]:
        doc_src = self.root / "doc" / "src"
        if not doc_src.is_dir():
            raise SourceError(f"LAMMPS docs not found: {doc_src}")

        # Map ".. index:: NAME" entries to their rst file (e.g. "fix nvt" -> fix_nh.rst).
        mapping: dict[str, Path] = {}
        for path in sorted(doc_src.glob("*.rst")):
            head = path.read_text(encoding="utf-8", errors="replace")[:3000]
            for entry in re.findall(r"^\.\. index:: (.+)$", head, flags=re.M):
                mapping.setdefault(entry.strip(), path)

        wanted = list(CORE_COMMANDS)
        wanted += [name for name, _ in self._user_commands().most_common() if name not in wanted]

        entries, seen = [], set()
        for name in wanted:
            path = mapping.get(name)
            if path is None or path in seen:
                continue
            seen.add(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            syntax = compact(rst_section(text, "Syntax"), 14)
            if not syntax:
                continue
            examples = compact(rst_section(text, "Examples"), 6)
            entries.append(f"### {name}  ({path.name})\n{syntax}\nExamples:\n{examples}\n")
            if len(entries) >= max_commands:
                break

        header = (
            f"# LAMMPS cheat sheet (local {self.version} docs, verbatim excerpts)\n"
            f"# Built {now_iso()}. Rebuild with /source {self.name} cheatsheet.\n\n"
        )
        self.cheatsheet_path.parent.mkdir(parents=True, exist_ok=True)
        self.cheatsheet_path.write_text(header + "\n".join(entries), encoding="utf-8")
        return {"path": str(self.cheatsheet_path), "commands": len(entries)}
