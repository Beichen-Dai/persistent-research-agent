"""Shared fixtures: a scripted fake LLM client and small sample corpora."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from research_agent.config import Config, SourceConfig


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
    )


def reply(content: str | None = None, tool_calls: list[Any] | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Mimics ``client.chat.completions.create`` with scripted replies."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.replies.pop(0)
        return item(kwargs) if callable(item) else item


@pytest.fixture
def notes_dir(tmp_path: Path) -> Path:
    root = tmp_path / "notes"
    (root / "sub").mkdir(parents=True)
    (root / "carbon.md").write_text(
        "# Carbon annealing\n\nGraphitization increases with annealing temperature.\n\n"
        "At 3000 K the sp2 fraction exceeds 90 percent.\n",
        encoding="utf-8",
    )
    (root / "sub" / "copper.txt").write_text(
        "Amorphous copper recrystallises during slow quenching.\n", encoding="utf-8"
    )
    (root / "ignored.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return root


def write_text_pdf(path: Path, lines: list[str]) -> None:
    """Write a minimal one-page PDF whose text pypdf can extract."""
    text_ops = " ".join(f"({line}) Tj 0 -16 Td" for line in lines)
    stream = f"BT /F1 12 Tf 72 720 Td {text_ops} ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))


@pytest.fixture
def pdf_dir(tmp_path: Path) -> Path:
    root = tmp_path / "library" / "ABC123"
    root.mkdir(parents=True)
    write_text_pdf(
        root / "Smith 2020 - Graphitization of carbide derived carbon.pdf",
        ["Carbide derived carbon was annealed at 3000 K.", "The porosity was 58 percent."],
    )
    write_text_pdf(
        root / "Lee 2021 - Copper glass.pdf",
        ["Amorphous copper forms below the critical cooling rate."],
    )
    return tmp_path / "library"


@pytest.fixture
def lammps_tree(tmp_path: Path) -> Path:
    root = tmp_path / "lammps-test"
    doc = root / "doc" / "src"
    doc.mkdir(parents=True)
    (doc / "fix_nh.rst").write_text(
        ".. index:: fix nvt\n.. index:: fix npt\n\n"
        "fix nvt command\n===============\n\n"
        'Syntax\n""""""\n\n.. code-block:: LAMMPS\n\n'
        "   fix ID group-ID nvt temp Tstart Tstop Tdamp\n\n"
        'Examples\n""""""""\n\n.. code-block:: LAMMPS\n\n   fix 1 all nvt temp 300.0 300.0 0.1\n\n'
        'Description\n"""""""""""\n\nNose-Hoover thermostat.\n',
        encoding="utf-8",
    )
    example = root / "examples" / "melt"
    example.mkdir(parents=True)
    (example / "in.melt").write_text(
        "units lj\natom_style atomic\npair_style lj/cut 2.5\nfix 1 all nve\nrun 250\n",
        encoding="utf-8",
    )
    (example / "README").write_text("Melt a 3d Lennard-Jones solid.\n", encoding="utf-8")
    return root


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path / "data", workspace=tmp_path / "workspace")


@pytest.fixture
def config_with_notes(config: Config, notes_dir: Path) -> Config:
    config.sources.append(
        SourceConfig(
            name="notes", type="text_folder", options={"path": str(notes_dir), "cards": True}
        )
    )
    return config
