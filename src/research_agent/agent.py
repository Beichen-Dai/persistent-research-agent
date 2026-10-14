"""The agent loop: build the context, call the model, run tools, repeat."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from research_agent import workflow
from research_agent.config import Config
from research_agent.history import (
    clip_tool_outputs,
    repair_conversation,
    select_recent,
    summarize_history,
)
from research_agent.knowledge.cards import CardStore
from research_agent.prompts import build_system_prompt
from research_agent.sources.base import DataSource
from research_agent.store import AgentStore
from research_agent.tools import Tool, ToolContext, available_tools

ToolCallback = Callable[[str, dict[str, Any]], None]


class ResearchAgent:
    """A tool-using agent bound to one configuration and a set of data sources.

    Args:
        config: Loaded configuration.
        client: An OpenAI-compatible client (``client.chat.completions.create``).
        sources: Data sources by name.
        on_tool_call: Optional callback ``(name, arguments)`` invoked before
            each tool runs (the CLI uses it to print tool activity).
    """

    def __init__(
        self,
        config: Config,
        client: Any,
        sources: dict[str, DataSource] | None = None,
        on_tool_call: ToolCallback | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.sources = sources or {}
        self.cards = CardStore(config.cards_db, config.agent.card_keyword_languages)
        self.on_tool_call = on_tool_call
        self.system_prompt = build_system_prompt(self.sources)

    # -- stores ---------------------------------------------------------------

    def open_store(self, project: str, session: str) -> AgentStore:
        """Open a project/session and repair an interrupted tool-call chain."""
        store = AgentStore(self.config.projects_dir, project, session)
        store.conversation, changed = repair_conversation(store.conversation)
        if changed:
            store.save()
        return store

    def context(self, store: AgentStore) -> ToolContext:
        return ToolContext(config=self.config, store=store, sources=self.sources, cards=self.cards)

    def tools(self, store: AgentStore) -> list[Tool]:
        disabled = workflow.disabled_tools(store.project_state)
        return [t for t in available_tools(self.context(store)) if t.name not in disabled]

    # -- context --------------------------------------------------------------

    def resident_knowledge(self) -> str | None:
        """Knowledge sent with every request.

        It is kept stable between turns and placed right after the system
        prompt, so the API's prefix cache can reuse it.
        """
        parts = []
        notes = self.config.core_notes
        if notes and notes.exists():
            parts.append(
                "=== CORE NOTES (user-written, highest priority) ===\n"
                + notes.read_text(encoding="utf-8", errors="replace")
            )
        catalog = self.cards.catalog()
        if catalog:
            parts.append(
                "=== DOCUMENT CATALOG (one line per knowledge card; get_knowledge_cards gives "
                "details) ===\n" + catalog
            )
        parts += [text for s in self.sources.values() if (text := s.resident_context())]
        if not parts:
            return None
        return "CORE KNOWLEDGE (always loaded)\n\n" + "\n\n".join(parts)

    def build_messages(self, store: AgentStore) -> list[dict[str, Any]]:
        settings = self.config.agent
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

        resident = self.resident_knowledge()
        if resident:
            messages.append({"role": "system", "content": resident})

        state = {k: v for k, v in store.project_state.items() if k != "workflow"}
        context = {
            "project": store.project,
            "session": store.session,
            "project_state": state,
            "global_memory": store.global_memory[-50:],
            "project_memory": store.project_memory[-50:],
            "session_memory": store.session_memory[-30:],
        }
        messages.append(
            {
                "role": "system",
                "content": "CURRENT PERSISTENT RESEARCH CONTEXT:\n"
                + json.dumps(context, ensure_ascii=False, indent=2, default=str),
            }
        )

        last_user = next(
            (
                m.get("content") or ""
                for m in reversed(store.conversation)
                if m.get("role") == "user"
            ),
            "",
        )
        if last_user and settings.auto_cards > 0:
            cards = self.cards.auto_context(last_user, settings.auto_cards)
            if cards:
                messages.append({"role": "system", "content": cards})

        phase = workflow.describe(store.project_state)
        if phase:
            messages.append({"role": "system", "content": phase})

        history = store.conversation
        recent = select_recent(history, settings.max_recent_messages)
        digest = summarize_history(history[: len(history) - len(recent)], settings.max_digest_chars)
        if digest:
            messages.append(
                {
                    "role": "system",
                    "content": "EARLIER CONVERSATION IN THIS SESSION (digest, tool outputs "
                    "omitted):\n" + digest,
                }
            )
        messages.extend(clip_tool_outputs(recent, settings.max_tool_chars))
        return messages

    # -- tools ----------------------------------------------------------------

    def execute_tool(self, store: AgentStore, name: str, arguments: dict[str, Any]) -> Any:
        if self.on_tool_call:
            self.on_tool_call(name, arguments)

        if name in workflow.disabled_tools(store.project_state):
            phase, _ = workflow.current_phase(store.project_state)
            return {
                "error": "TOOL_DISABLED_IN_PHASE",
                "message": f"'{name}' is disabled in the current phase '{phase}'. "
                "Follow the phase rules.",
            }

        tools = {t.name: t for t in available_tools(self.context(store))}
        if name not in tools:
            return {"error": f"Unknown tool: {name}"}
        try:
            return tools[name].handler(self.context(store), arguments)
        except Exception as exc:
            return {"error": type(exc).__name__, "message": str(exc)}

    # -- main loop ------------------------------------------------------------

    def ask(self, store: AgentStore, user_message: str) -> str:
        """Add a user message, run the tool loop until the model answers, return the answer.

        The conversation is saved after every step, so an interruption
        (Ctrl+C) loses nothing; call :func:`repair_conversation` afterwards.
        """
        store.conversation.append({"role": "user", "content": user_message})
        store.save()

        messages = self.build_messages(store)
        while True:
            response = self.client.chat.completions.create(
                model=self.config.llm.model,
                messages=messages,
                tools=[t.schema() for t in self.tools(store)],
                tool_choice="auto",
            )
            reply = response.choices[0].message

            if not reply.tool_calls:
                content = reply.content or ""
                store.conversation.append({"role": "assistant", "content": content})
                store.save()
                return content

            assistant = {
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in reply.tool_calls
                ],
            }
            store.conversation.append(assistant)
            messages.append(assistant)

            for call in reply.tool_calls:
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    result: Any = {"error": "Invalid tool arguments", "message": str(exc)}
                else:
                    result = self.execute_tool(store, call.function.name, arguments)

                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
                store.conversation.append(tool_message)
                messages.append(tool_message)
                store.save()
