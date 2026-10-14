from pathlib import Path
from typing import Any

import pytest

from research_agent.config import SourceConfig
from research_agent.sources import DataSource, NotSupported, SearchHit, SourceError
from research_agent.sources.lammps_docs import LammpsDocsSource
from research_agent.sources.pdf_folder import PdfFolderSource, split_sentences
from research_agent.sources.registry import build_sources, resolve_source_class
from research_agent.sources.text_folder import TextFolderSource


class EchoSource(DataSource):
    """Minimal custom source used to test the plug-in mechanism."""

    evidence_label = "DATA"

    def search(self, query: str, limit: int = 10, filters: dict[str, Any] | None = None):
        return [SearchHit(doc_id="1", title="echo", text=query, location="row 1")]


# -- text folder -------------------------------------------------------------


def test_text_folder_index_search_read(tmp_path, notes_dir):
    source = TextFolderSource("notes", tmp_path / "idx", path=str(notes_dir))
    summary = source.index()
    assert summary["indexed"] == 2  # *.md and *.txt only
    assert source.index()["unchanged"] == 2  # incremental

    hits = source.search("graphitization")
    assert hits and hits[0].title == "Carbon annealing"
    doc = source.read(hits[0].doc_id)
    assert "sp2 fraction" in doc.text
    assert {d.metadata["path"] for d in source.list_documents()} == {"carbon.md", "sub/copper.txt"}
    assert source.status()["documents"] == 2


def test_text_folder_detects_removed_and_changed_files(tmp_path, notes_dir):
    source = TextFolderSource("notes", tmp_path / "idx", path=str(notes_dir))
    source.index()
    (notes_dir / "sub" / "copper.txt").unlink()
    (notes_dir / "carbon.md").write_text("# Carbon\n\nNew text about porosity.\n")
    summary = source.index()
    assert summary == {"found": 1, "indexed": 1, "unchanged": 0, "removed": 1}
    assert source.search("porosity") and not source.search("recrystallises")


def test_text_folder_requires_path(tmp_path):
    with pytest.raises(SourceError, match="path"):
        TextFolderSource("notes", tmp_path)


# -- PDF folder --------------------------------------------------------------


def test_pdf_folder_full_text_and_title_search(tmp_path, pdf_dir):
    source = PdfFolderSource("papers", tmp_path / "idx", path=str(pdf_dir))
    # Title search works before indexing.
    by_title = source.search("copper glass", filters={"mode": "title"})
    assert by_title and "Copper" in by_title[0].title

    summary = source.index()
    assert summary["indexed"] == 2 and not summary["errors"]

    hits = source.search("porosity carbon")
    assert hits[0].location == "p. 1"
    assert "58 percent" in hits[0].text
    doc = source.read(hits[0].doc_id)
    assert doc.text.startswith("[p.1]")
    assert source.status()["pages"] == 2


def test_pdf_folder_read_rejects_paths_outside_folder(tmp_path, pdf_dir):
    source = PdfFolderSource("papers", tmp_path / "idx", path=str(pdf_dir))
    with pytest.raises(SourceError):
        source.read("/etc/passwd")


def test_pdf_card_support(tmp_path, pdf_dir):
    source = PdfFolderSource("papers", tmp_path / "idx", path=str(pdf_dir))
    source.index()
    candidates = list(source.card_candidates())
    assert len(candidates) == 2
    assert "[p.1]" in source.card_text(candidates[0].doc_id, 1000)


def test_split_sentences_respects_limit():
    chunks = split_sentences("A short one. " + "x" * 50, max_chars=20)
    assert all(len(c) <= 20 for c in chunks)


# -- LAMMPS docs ---------------------------------------------------------------


def test_lammps_docs(tmp_path, lammps_tree):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "in.run").write_text("fix 1 all nvt temp 300 300 0.1\n")
    source = LammpsDocsSource(
        "lammps", tmp_path / "idx", path=str(lammps_tree), script_dirs=[str(scripts)]
    )
    assert source.index() | {"built": None} == {
        "root": str(lammps_tree),
        "docs": 1,
        "examples": 1,
        "built": None,
    }

    docs = source.search("nvt thermostat", filters={"doc_type": "doc"})
    assert docs and docs[0].metadata["label"] == "DOC"
    examples = source.search("lj/cut", filters={"doc_type": "example"})
    assert examples and examples[0].title == "melt/in.melt"
    assert "Melt a 3d" in source.read(examples[0].doc_id).text

    result = source.build_cheatsheet()
    assert result["commands"] == 1
    assert "fix ID group-ID nvt" in source.resident_context()
    assert "LAMMPS" in source.instructions()


def test_lammps_docs_rejects_non_tree(tmp_path):
    source = LammpsDocsSource("lammps", tmp_path / "idx", path=str(tmp_path))
    with pytest.raises(SourceError, match="Not a LAMMPS source tree"):
        source.index()


# -- interface and registry ----------------------------------------------------


def test_optional_capabilities_and_describe(tmp_path):
    source = EchoSource("db", tmp_path, description="test")
    assert source.capabilities() == ["search"]
    with pytest.raises(NotSupported):
        source.read("1")
    assert source.describe()["type"].endswith(":EchoSource")
    assert source.search("hello")[0].to_dict()["text"] == "hello"


def test_registry_resolves_builtin_and_import_path():
    assert resolve_source_class("text_folder") is TextFolderSource
    custom = resolve_source_class("tests.test_sources:EchoSource")
    assert custom.__name__ == "EchoSource" and issubclass(custom, DataSource)
    with pytest.raises(SourceError, match="Unknown data source type"):
        resolve_source_class("nope")
    with pytest.raises(SourceError, match="not a subclass"):
        resolve_source_class("pathlib:Path")


def test_build_sources_reports_errors(config, notes_dir):
    config.sources = [
        SourceConfig("notes", "text_folder", {"path": str(notes_dir), "description": "N"}),
        SourceConfig("broken", "text_folder", {}),
        SourceConfig("off", "text_folder", {"path": "x"}, enabled=False),
    ]
    sources, errors = build_sources(config)
    assert list(sources) == ["notes"]
    assert sources["notes"].description == "N"
    assert sources["notes"].data_dir == config.sources_dir / "notes"
    assert "broken" in errors


def test_fts_tokens_are_safe():
    from research_agent.sources.fts import match_expression, split_paragraphs

    assert match_expression('fix "nvt" (temp)', "AND") == '"fix" AND "nvt" AND "temp"'
    assert match_expression("!!!") == ""
    assert split_paragraphs("a\n\nb", 100) == ["a\n\nb"]
    assert all(len(c) <= 5 for c in split_paragraphs("x" * 12, 5))


def test_source_without_index(tmp_path: Path):
    assert EchoSource("e", tmp_path).index() == {"status": "no index needed"}
