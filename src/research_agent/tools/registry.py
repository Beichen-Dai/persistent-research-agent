"""Tool definitions shared by all tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from research_agent.config import Config
    from research_agent.knowledge.cards import CardStore
    from research_agent.sources.base import DataSource
    from research_agent.store import AgentStore


@dataclass
class ToolContext:
    """Everything a tool handler may need."""

    config: Config
    store: AgentStore
    sources: dict[str, DataSource] = field(default_factory=dict)
    cards: CardStore | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], Any]

    def schema(self) -> dict[str, Any]:
        """OpenAI-style function-tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def params(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """JSON-schema helper for tool parameters."""
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema
