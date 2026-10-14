from research_agent.history import clip, repair_conversation, select_recent, summarize_history


def call(call_id):
    return {"id": call_id, "type": "function", "function": {"name": "x", "arguments": "{}"}}


HISTORY = [
    {"role": "user", "content": "first question"},
    {"role": "assistant", "content": None, "tool_calls": [call("a"), call("b")]},
    {"role": "tool", "tool_call_id": "a", "content": "result a"},
    {"role": "tool", "tool_call_id": "b", "content": "result b"},
    {"role": "assistant", "content": "first answer"},
]


def test_select_recent_walks_back_to_tool_call():
    selected = select_recent(HISTORY, 2)  # would start at the tool result "b"
    assert selected[0]["role"] == "assistant"
    assert selected[0]["tool_calls"]


def test_select_recent_drops_orphaned_tool_results():
    history = HISTORY[2:]  # the assistant tool-call message is gone
    selected = select_recent(history, 3)
    assert selected[0]["role"] == "assistant"
    assert selected[0]["content"] == "first answer"


def test_summarize_history_drops_agent_replies_first():
    older = [
        {"role": "user", "content": "u" * 100},
        {"role": "assistant", "content": "a" * 100},
        {"role": "tool", "content": "ignored"},
    ]
    digest = summarize_history(older, max_chars=120)
    assert digest.startswith("USER:")
    assert "AGENT" not in digest


def test_repair_adds_missing_tool_results():
    broken = HISTORY[:3]  # result for call "b" missing
    repaired, changed = repair_conversation(broken)
    assert changed
    assert [m.get("tool_call_id") for m in repaired if m["role"] == "tool"] == ["a", "b"]
    assert "INTERRUPTED" in repaired[-1]["content"]


def test_repair_leaves_valid_history_alone():
    repaired, changed = repair_conversation(HISTORY)
    assert not changed
    assert repaired == HISTORY


def test_clip():
    assert clip("abc", 5) == "abc"
    assert clip("abcdefgh", 3).startswith("abc\n...[clipped 5 chars]")
