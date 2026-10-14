"""Example custom data source: rows of a table in any SQLite database.

This file shows how to connect your own database to the agent. Copy it,
adapt the queries to your schema (or to PostgreSQL, MySQL, an HTTP API, ...),
and register the class in ``research-agent.toml``::

    [[sources]]
    name = "measurements"
    type = "sqlite_table_source:SqliteTableSource"   # module must be importable
    description = "Measured adsorption isotherms, one row per experiment"
    database = "/data/lab.sqlite"
    table = "experiments"
    id_column = "id"
    title_column = "sample"
    text_columns = ["summary", "conditions", "notes"]

Make the module importable, e.g. ``export PYTHONPATH=/path/to/this/folder``,
or put the class in an installed package.

Only ``search`` is required; ``read``, ``list_documents`` and ``status`` are
optional and enable the matching agent tools for this source.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from research_agent.sources import (
    DataSource,
    Document,
    DocumentInfo,
    SearchHit,
    SourceError,
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: str) -> str:
    """Reject anything that is not a plain SQL identifier (prevents injection)."""
    if not _IDENTIFIER.match(value):
        raise SourceError(f"Invalid SQL identifier: {value!r}")
    return value


class SqliteTableSource(DataSource):
    """Keyword search over selected text columns of one SQLite table."""

    evidence_label = "DATA"
    filters_help = '{"column": "<name>"} restricts the search to one text column.'

    def __init__(self, name: str, data_dir: Path, description: str = "", **options: Any) -> None:
        super().__init__(name, data_dir, description or "Rows of a SQLite table", **options)
        try:
            self.database = Path(options["database"]).expanduser()
            self.table = _identifier(options["table"])
            self.id_column = _identifier(options.get("id_column", "rowid"))
            self.title_column = _identifier(options.get("title_column", self.id_column))
            self.text_columns = [_identifier(c) for c in options["text_columns"]]
        except KeyError as exc:
            raise SourceError(f"Source '{name}': missing option {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        if not self.database.exists():
            raise SourceError(f"Database not found: {self.database}")
        # Read-only: the agent must never modify your data.
        conn = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_text(self, row: sqlite3.Row) -> str:
        return "\n".join(f"{c}: {row[c]}" for c in self.text_columns if row[c] is not None)

    def search(
        self, query: str, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[SearchHit]:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
        if not words:
            return []
        columns = self.text_columns
        if filters and filters.get("column"):
            column = _identifier(filters["column"])
            if column not in self.text_columns:
                raise SourceError(f"Column '{column}' is not a configured text column.")
            columns = [column]

        # Score = number of query words found in the row (case-insensitive).
        haystack = " || ' ' || ".join(f"COALESCE({c}, '')" for c in columns)
        score = " + ".join(f"(instr(lower({haystack}), ?) > 0)" for _ in words)
        sql = (
            f"SELECT *, ({score}) AS score FROM {self.table} "
            f"WHERE ({score}) > 0 ORDER BY score DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, (*words, *words, limit)).fetchall()
        return [
            SearchHit(
                doc_id=str(row[self.id_column]),
                title=str(row[self.title_column]),
                text=self._row_text(row),
                location=f"{self.table}.{self.id_column}={row[self.id_column]}",
                score=float(row["score"]),
            )
            for row in rows
        ]

    def read(self, doc_id: str, max_chars: int = 30_000) -> Document:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {self.table} WHERE {self.id_column} = ?", (doc_id,)
            ).fetchone()
        if row is None:
            raise SourceError(f"No row with {self.id_column} = {doc_id}")
        # sqlite3.Row iterates over values, so .keys() is needed here.
        text = "\n".join(f"{key}: {row[key]}" for key in row.keys())  # noqa: SIM118
        return Document(
            doc_id, str(row[self.title_column]), text[:max_chars], truncated=len(text) > max_chars
        )

    def list_documents(self, limit: int = 50, offset: int = 0) -> list[DocumentInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self.id_column} AS id, {self.title_column} AS title "
                f"FROM {self.table} LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [DocumentInfo(str(r["id"]), str(r["title"])) for r in rows]

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            count = conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]
        return {"database": str(self.database), "table": self.table, "rows": count}
