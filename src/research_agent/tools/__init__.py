"""Tools exposed to the model."""

from __future__ import annotations

from research_agent.tools.core import CORE_TOOLS
from research_agent.tools.data import CARD_TOOLS, SOURCE_TOOLS
from research_agent.tools.registry import Tool, ToolContext


def available_tools(ctx: ToolContext) -> list[Tool]:
    """All tools that make sense for the current configuration."""
    tools = list(CORE_TOOLS)
    if ctx.sources:
        tools += SOURCE_TOOLS
    if ctx.cards is not None and ctx.cards.path.exists():
        tools += CARD_TOOLS
    return tools


__all__ = ["Tool", "ToolContext", "available_tools"]
