"""Keeps the custom-source example in examples/ working."""

import sqlite3
import sys
from pathlib import Path

import pytest

from research_agent.config import SourceConfig
from research_agent.sources import SourceError
from research_agent.sources.registry import build_sources

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "custom_source"


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "lab.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE experiments (id INTEGER PRIMARY KEY, sample TEXT, summary TEXT)")
        conn.executemany(
            "INSERT INTO experiments (sample, summary) VALUES (?, ?)",
            [("TiC-CDC-800", "Argon uptake 18 mmol/g at 77 K"), ("Cu-film", "XRD shows fcc peaks")],
        )
    return path


def test_sqlite_example_through_registry(config, database, monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLES))
    config.sources = [
        SourceConfig(
            "lab",
            "sqlite_table_source:SqliteTableSource",
            {
                "database": str(database),
                "table": "experiments",
                "id_column": "id",
                "title_column": "sample",
                "text_columns": ["summary"],
            },
        )
    ]
    sources, errors = build_sources(config)
    assert not errors
    lab = sources["lab"]
    hits = lab.search("argon uptake")
    assert hits[0].title == "TiC-CDC-800"
    assert "18 mmol/g" in lab.read(hits[0].doc_id).text
    assert len(lab.list_documents()) == 2
    assert lab.status()["rows"] == 2
    assert lab.capabilities() == ["search", "read", "list_documents"]


def test_sqlite_example_rejects_sql_injection(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLES))
    sys.modules.pop("sqlite_table_source", None)
    from sqlite_table_source import SqliteTableSource

    with pytest.raises(SourceError, match="Invalid SQL identifier"):
        SqliteTableSource("x", tmp_path, database="db", table="t; DROP TABLE t", text_columns=["a"])
