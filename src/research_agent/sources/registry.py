"""Create data sources from the configuration.

The ``type`` of a ``[[sources]]`` entry can be:

* a built-in type name: ``pdf_folder``, ``text_folder``, ``lammps_docs``;
* an import path ``"package.module:ClassName"`` for your own class;
* the name of an installed plugin that registers its class under the entry-point
  group ``research_agent.sources`` (``[project.entry-points."research_agent.sources"]``
  in the plugin's ``pyproject.toml``).
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import Any

from research_agent.config import Config, SourceConfig
from research_agent.sources.base import DataSource, SourceError
from research_agent.sources.lammps_docs import LammpsDocsSource
from research_agent.sources.pdf_folder import PdfFolderSource
from research_agent.sources.text_folder import TextFolderSource

ENTRY_POINT_GROUP = "research_agent.sources"

BUILTIN_SOURCES: dict[str, type[DataSource]] = {
    cls.type_name: cls for cls in (PdfFolderSource, TextFolderSource, LammpsDocsSource)
}


def resolve_source_class(type_name: str) -> type[DataSource]:
    """Return the DataSource subclass for a configured ``type``."""
    if type_name in BUILTIN_SOURCES:
        return BUILTIN_SOURCES[type_name]

    cls: Any = None
    if ":" in type_name:
        module_name, _, attr = type_name.partition(":")
        try:
            cls = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError) as exc:
            raise SourceError(f"Cannot import data source '{type_name}': {exc}") from exc
    else:
        matches = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == type_name]
        if not matches:
            known = ", ".join(sorted(BUILTIN_SOURCES))
            raise SourceError(
                f"Unknown data source type '{type_name}'. Built-in types: {known}; "
                "or use 'package.module:ClassName'."
            )
        cls = matches[0].load()

    if not (isinstance(cls, type) and issubclass(cls, DataSource)):
        raise SourceError(f"'{type_name}' is not a subclass of research_agent.sources.DataSource.")
    return cls


def create_source(config: Config, entry: SourceConfig) -> DataSource:
    cls = resolve_source_class(entry.type)
    options = dict(entry.options)
    description = options.pop("description", "")
    return cls(entry.name, config.sources_dir / entry.name, description=description, **options)


def build_sources(config: Config) -> tuple[dict[str, DataSource], dict[str, str]]:
    """Create all enabled sources.

    Returns ``(sources, errors)``: a source that fails to load is reported in
    ``errors`` instead of stopping the agent.
    """
    sources: dict[str, DataSource] = {}
    errors: dict[str, str] = {}
    for entry in config.sources:
        if not entry.enabled:
            continue
        try:
            sources[entry.name] = create_source(config, entry)
        except Exception as exc:
            errors[entry.name] = f"{type(exc).__name__}: {exc}"
    return sources, errors
