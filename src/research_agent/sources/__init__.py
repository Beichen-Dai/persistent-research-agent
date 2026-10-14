"""Pluggable data sources. See :mod:`research_agent.sources.base` for the interface."""

from research_agent.sources.base import (
    CardCandidate,
    DataSource,
    Document,
    DocumentInfo,
    NotSupported,
    SearchHit,
    SourceError,
)

__all__ = [
    "CardCandidate",
    "DataSource",
    "Document",
    "DocumentInfo",
    "NotSupported",
    "SearchHit",
    "SourceError",
]
