"""Persistent storage of projects, sessions and memories (plain JSON files).

Layout under ``<data_dir>/projects``::

    global_memory.json
    <project>/
        project_state.json
        project_memory.json
        jobs/                       (see research_agent.jobs)
        sessions/<session>/
            conversation.json
            session_memory.json
            archive/                (cleared conversations)
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    """Read JSON, returning ``default`` if the file is missing or unreadable."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data: Any) -> None:
    """Write JSON atomically (write a temporary file, then rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # errors="replace": stray surrogates from terminal input or program
    # output must not make saving fail.
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    tmp.write_bytes(text.encode("utf-8", errors="replace"))
    tmp.replace(path)


def safe_name(name: str) -> str:
    """Turn a user-given project/session name into a safe directory name."""
    name = str(name).strip()
    if not name:
        raise ValueError("Project and session names cannot be empty.")
    return _SAFE_NAME.sub("_", name)[:100]


class AgentStore:
    """State of one session inside one project."""

    def __init__(self, root: Path, project: str, session: str) -> None:
        self.root = Path(root)
        self.project = safe_name(project)
        self.session = safe_name(session)

        self.project_dir = self.root / self.project
        self.session_dir = self.project_dir / "sessions" / self.session
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self._paths = {
            "global_memory": self.root / "global_memory.json",
            "project_state": self.project_dir / "project_state.json",
            "project_memory": self.project_dir / "project_memory.json",
            "conversation": self.session_dir / "conversation.json",
            "session_memory": self.session_dir / "session_memory.json",
        }

        self.global_memory: list[dict[str, Any]] = load_json(self._paths["global_memory"], [])
        self.project_state: dict[str, Any] = load_json(
            self._paths["project_state"], {"project": self.project, "created": now_iso()}
        )
        self.project_memory: list[dict[str, Any]] = load_json(self._paths["project_memory"], [])
        self.conversation: list[dict[str, Any]] = load_json(self._paths["conversation"], [])
        self.session_memory: list[dict[str, Any]] = load_json(self._paths["session_memory"], [])

    def save(self) -> None:
        for key, path in self._paths.items():
            save_json(path, getattr(self, key))

    def archive_conversation(self) -> Path | None:
        """Move the current conversation into the session archive and clear it."""
        archived = None
        if self.conversation:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archived = self.session_dir / "archive" / f"conversation_{stamp}.json"
            save_json(archived, self.conversation)
        self.conversation = []
        self.save()
        return archived

    def list_projects(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def list_sessions(self) -> list[str]:
        return sorted(p.name for p in (self.project_dir / "sessions").iterdir() if p.is_dir())
