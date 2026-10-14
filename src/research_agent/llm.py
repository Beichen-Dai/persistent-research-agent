"""Client for an OpenAI-compatible chat-completions API (DeepSeek by default)."""

from __future__ import annotations

import os
from typing import Any

from research_agent.config import ConfigError, LLMConfig


def create_client(config: LLMConfig) -> Any:
    """Create an OpenAI-compatible client.

    The API key is read from the environment variable named in
    ``config.api_key_env``; it is never read from or written to disk.
    """
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise ConfigError(
            f"The environment variable {config.api_key_env} is not set. "
            f"Export your API key first, e.g.\n    export {config.api_key_env}=..."
        )

    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=config.base_url)
