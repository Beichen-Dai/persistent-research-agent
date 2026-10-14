"""Configuration loading.

Settings come from a TOML file (``research-agent.toml`` by default). The API
key is never stored in the file: the file only names the environment variable
that holds it (``llm.api_key_env``).

Lookup order for the configuration file:

1. the path given with ``--config``;
2. the ``RESEARCH_AGENT_CONFIG`` environment variable;
3. ``research-agent.toml`` in the current directory.

If no file is found, built-in defaults are used (no data sources).
Relative paths in the file are resolved against the file's directory.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CONFIG_ENV_VAR = "RESEARCH_AGENT_CONFIG"
DEFAULT_CONFIG_NAME = "research-agent.toml"


class ConfigError(Exception):
    """Raised for an invalid or incomplete configuration."""


@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    api_key_env: str = "DEEPSEEK_API_KEY"
    # Model used to build knowledge cards; defaults to ``model``.
    card_model: str | None = None


@dataclass
class AgentConfig:
    max_recent_messages: int = 40
    max_digest_chars: int = 30_000
    max_tool_chars: int = 20_000
    run_python_timeout: int = 120
    auto_cards: int = 3
    # Extra languages for card keywords, so questions in those languages
    # match papers written in English (e.g. ["Chinese"]).
    card_keyword_languages: list[str] = field(default_factory=list)


@dataclass
class SourceConfig:
    name: str
    type: str
    options: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class Config:
    data_dir: Path
    workspace: Path
    core_notes: Path | None = None
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    sources: list[SourceConfig] = field(default_factory=list)
    path: Path | None = None

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def cards_db(self) -> Path:
        return self.data_dir / "knowledge" / "cards.db"


def find_config_file(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Return the configuration file to use, or None for built-in defaults."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"Configuration file not found: {path}")
        return path.resolve()

    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        path = Path(env_path).expanduser()
        if not path.is_file():
            raise ConfigError(f"{CONFIG_ENV_VAR} points to a missing file: {path}")
        return path.resolve()

    local = Path.cwd() / DEFAULT_CONFIG_NAME
    return local.resolve() if local.is_file() else None


def _resolve(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(os.path.expandvars(value)).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _dataclass_from(cls: type, values: dict[str, Any], section: str) -> Any:
    known = set(cls.__dataclass_fields__)
    unknown = set(values) - known
    if unknown:
        raise ConfigError(f"Unknown key(s) in [{section}]: {', '.join(sorted(unknown))}")
    return cls(**values)


def load_config(explicit: str | os.PathLike[str] | None = None) -> Config:
    """Load the configuration (see module docstring for the lookup order)."""
    path = find_config_file(explicit)
    raw: dict[str, Any] = {}
    if path is not None:
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc

    base = path.parent if path else Path.cwd()
    paths = raw.get("paths", {})

    llm_raw = dict(raw.get("llm", {}))
    if "api_key" in llm_raw:
        raise ConfigError(
            "Do not put the API key in the configuration file. "
            "Set [llm] api_key_env to the name of an environment variable instead."
        )

    sources = []
    seen: set[str] = set()
    for entry in raw.get("sources", []):
        entry = dict(entry)
        name = entry.pop("name", None)
        type_ = entry.pop("type", None)
        if not name or not type_:
            raise ConfigError("Every [[sources]] entry needs 'name' and 'type'.")
        if name in seen:
            raise ConfigError(f"Duplicate source name: {name}")
        seen.add(name)
        enabled = bool(entry.pop("enabled", True))
        # Path-like options are resolved relative to the configuration file:
        # "path", "root", keys ending in "_path"/"_dir", and lists in "*_dirs".
        for key, value in list(entry.items()):
            if key in ("path", "root") or key.endswith(("_path", "_dir")):
                if isinstance(value, str):
                    entry[key] = str(_resolve(base, value))
            elif key.endswith("_dirs") and isinstance(value, list):
                entry[key] = [str(_resolve(base, v)) for v in value]
        sources.append(SourceConfig(name=name, type=type_, options=entry, enabled=enabled))

    return Config(
        data_dir=_resolve(base, paths.get("data_dir", "agent-data")),
        workspace=_resolve(base, paths.get("workspace", "workspace")),
        core_notes=_resolve(base, paths.get("core_notes")),
        llm=_dataclass_from(LLMConfig, llm_raw, "llm"),
        agent=_dataclass_from(AgentConfig, dict(raw.get("agent", {})), "agent"),
        sources=sources,
        path=path,
    )
