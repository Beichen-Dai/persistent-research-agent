"""Conversation-history handling for the chat-completions API.

The model sees the most recent messages in full. Older messages are reduced
to a digest of what the user asked and what the agent answered (tool outputs
are left out), so long projects keep their context without unbounded growth.
"""

from __future__ import annotations

import json
from typing import Any

Message = dict[str, Any]


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[clipped {len(text) - limit} chars]"


def select_recent(history: list[Message], max_messages: int) -> list[Message]:
    """Return the most recent messages without breaking a tool-call chain.

    A valid unit is ``assistant(tool_calls)`` followed by its ``tool`` results.
    The selection never starts with a tool result whose assistant message was
    cut off: it walks back to that message, or drops the orphaned results.
    """
    selected = history[-max_messages:] if max_messages > 0 else []
    if not selected or selected[0].get("role") != "tool":
        return selected

    first_id = selected[0].get("tool_call_id")
    start = len(history) - len(selected)
    for i in range(start - 1, -1, -1):
        msg = history[i]
        if msg.get("role") != "assistant":
            continue
        ids = {tc.get("id") for tc in msg.get("tool_calls") or [] if isinstance(tc, dict)}
        if first_id in ids:
            return history[i:]

    while selected and selected[0].get("role") == "tool":
        selected = selected[1:]
    return selected


def summarize_history(older: list[Message], max_chars: int) -> str:
    """Digest of older user/agent messages that fits into ``max_chars``.

    The oldest agent replies are dropped first, then the oldest user messages.
    """
    entries: list[list[str]] = []
    for msg in older:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            entries.append([role, clip(content, 2000 if role == "user" else 500)])

    total = sum(len(text) + 8 for _, text in entries)
    for drop_role in ("assistant", "user"):
        i = 0
        while total > max_chars and i < len(entries):
            if entries[i][0] == drop_role:
                total -= len(entries[i][1]) + 8
                entries.pop(i)
            else:
                i += 1

    return "\n".join(f"{'USER' if r == 'user' else 'AGENT'}: {t}" for r, t in entries)


def clip_tool_outputs(messages: list[Message], limit: int) -> list[Message]:
    out = []
    for msg in messages:
        if msg.get("role") == "tool":
            msg = {**msg, "content": clip(msg.get("content") or "", limit)}
        out.append(msg)
    return out


def repair_conversation(history: list[Message]) -> tuple[list[Message], bool]:
    """Give every assistant tool call a tool result.

    A call left without a result (agent interrupted or crashed mid-tool) would
    make every later API request fail. Returns ``(repaired, changed)``.
    """
    repaired: list[Message] = []
    i = 0
    while i < len(history):
        msg = history[i]
        repaired.append(msg)
        i += 1
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue

        answered = set()
        while i < len(history) and history[i].get("role") == "tool":
            answered.add(history[i].get("tool_call_id"))
            repaired.append(history[i])
            i += 1

        for call in msg["tool_calls"]:
            if call.get("id") not in answered:
                repaired.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(
                            {
                                "error": "INTERRUPTED",
                                "message": (
                                    "No result was recorded; the agent was interrupted. "
                                    "Check the state (e.g. list_jobs) before redoing anything."
                                ),
                            }
                        ),
                    }
                )

    return repaired, len(repaired) != len(history)
