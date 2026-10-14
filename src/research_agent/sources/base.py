"""The data-source interface.

A *data source* is anything the agent can search for evidence: a PDF library,
a folder of notes, software documentation, a SQL database, a web API, ...

To connect your own data, subclass :class:`DataSource`, implement
:meth:`DataSource.search` (and optionally the other methods), and register it
in the configuration file::

    [[sources]]
    name = "my_db"
    type = "my_package.my_module:MyDatabaseSource"
    description = "Measured adsorption isotherms"
    dsn = "postgresql://..."          # any extra keys go to __init__ as options

The agent then reaches every source through the same generic tools
(``search_sources``, ``read_document``, ``list_documents``), so no change to
the agent itself is needed. See ``docs/data-sources.md`` for a walkthrough.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar


class SourceError(Exception):
    """Raised by a data source for an invalid request or a broken backend."""


class NotSupported(SourceError):
    """Raised when a source does not implement an optional capability."""


@dataclass
class SearchHit:
    """One piece of evidence returned by :meth:`DataSource.search`."""

    doc_id: str
    title: str
    text: str
    location: str | None = None  # e.g. "p. 5", a section name or a row key
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # filled in by the agent

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, {}, "")}


@dataclass
class DocumentInfo:
    """A document listed by :meth:`DataSource.list_documents`."""

    doc_id: str
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Document:
    """The content of one document, returned by :meth:`DataSource.read`."""

    doc_id: str
    title: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CardCandidate:
    """A document that can be digested into a knowledge card."""

    doc_id: str
    title: str
    content_hash: str  # a new hash means the card is rebuilt


class DataSource(ABC):
    """Base class for all data sources.

    Required: :meth:`search`.

    Optional (the default raises :class:`NotSupported` or does nothing):
    :meth:`read`, :meth:`list_documents`, :meth:`index`, :meth:`status`,
    :meth:`instructions`, :meth:`resident_context`, :meth:`actions`,
    :meth:`card_candidates` / :meth:`card_text` (knowledge cards).

    Class attributes:

    ``type_name``
        Short type name used in the configuration (built-in sources only).
    ``evidence_label``
        Label the agent uses when it cites this source, e.g. ``PAPER``,
        ``DOC``, ``NOTE`` or ``DATA``.
    ``filters_help``
        Human-readable description of the ``filters`` that :meth:`search`
        accepts; shown to the model.
    """

    type_name: ClassVar[str] = ""
    evidence_label: ClassVar[str] = "SOURCE"
    filters_help: ClassVar[str] = ""

    def __init__(self, name: str, data_dir: Path, description: str = "", **options: Any) -> None:
        """
        Args:
            name: Instance name from the configuration (``[[sources]] name``).
            data_dir: Private directory for this source's index or cache.
            description: Shown to the model so it knows what the source holds.
            **options: Remaining keys of the ``[[sources]]`` entry.
        """
        self.name = name
        self.data_dir = Path(data_dir)
        self.description = description
        self.options = options

    # -- required -----------------------------------------------------------

    @abstractmethod
    def search(
        self, query: str, limit: int = 10, filters: dict[str, Any] | None = None
    ) -> list[SearchHit]:
        """Return the evidence most relevant to ``query``, best first."""

    # -- optional -----------------------------------------------------------

    def read(self, doc_id: str, max_chars: int = 30_000) -> Document:
        """Return the content of one document (``doc_id`` from a hit or listing)."""
        raise NotSupported(f"Source '{self.name}' does not support reading documents.")

    def list_documents(self, limit: int = 50, offset: int = 0) -> list[DocumentInfo]:
        """List documents held by the source."""
        raise NotSupported(f"Source '{self.name}' does not support listing documents.")

    def index(self, force: bool = False) -> dict[str, Any]:
        """Build or update a search index. Return a short summary."""
        return {"status": "no index needed"}

    def status(self) -> dict[str, Any]:
        """Return statistics about the source (sizes, index state, ...)."""
        return {}

    def instructions(self) -> str | None:
        """Extra guidance for the model, added to the system prompt."""
        return None

    def resident_context(self) -> str | None:
        """Short, stable text sent with every request (e.g. a cheat sheet)."""
        return None

    def actions(self) -> dict[str, tuple[Any, str]]:
        """Extra CLI actions: ``{name: (callable() -> result, help text)}``.

        They are run by the user with ``/source NAME ACTION``.
        """
        return {}

    def card_candidates(self) -> Iterator[CardCandidate]:
        """Documents that should get a knowledge card (empty: no cards)."""
        return iter(())

    def card_text(self, doc_id: str, max_chars: int) -> str:
        """Text used to build the knowledge card of ``doc_id``.

        Page or section markers such as ``[p.5]`` let the card cite locations.
        """
        raise NotSupported(f"Source '{self.name}' does not support knowledge cards.")

    # -- helpers ------------------------------------------------------------

    def capabilities(self) -> list[str]:
        """Names of the optional capabilities this source implements."""
        caps = ["search"]
        for method in ("read", "list_documents", "index", "card_text"):
            if getattr(type(self), method) is not getattr(DataSource, method):
                caps.append("knowledge_cards" if method == "card_text" else method)
        return caps

    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "name": self.name,
            "type": self.type_name or f"{type(self).__module__}:{type(self).__qualname__}",
            "evidence_label": self.evidence_label,
            "description": self.description,
            "capabilities": self.capabilities(),
        }
        if self.filters_help:
            info["filters"] = self.filters_help
        return info
