import json

from conftest import FakeClient, reply, tool_call
from research_agent import workflow
from research_agent.agent import ResearchAgent
from research_agent.knowledge.cards import CardStore
from research_agent.sources.registry import build_sources


def make_agent(config, replies):
    sources, errors = build_sources(config)
    assert not errors
    for source in sources.values():
        source.index()
    calls = []
    agent = ResearchAgent(config, FakeClient(replies), sources, lambda n, a: calls.append(n))
    return agent, calls


def test_tool_loop_searches_and_answers(config_with_notes):
    agent, calls = make_agent(
        config_with_notes,
        [
            reply(tool_calls=[tool_call("c1", "search_sources", {"query": "graphitization"})]),
            reply("Graphitization increases with temperature [NOTE]."),
        ],
    )
    store = agent.open_store("p", "s")
    answer = agent.ask(store, "What happens to graphitization?")

    assert answer.startswith("Graphitization")
    assert calls == ["search_sources"]
    roles = [m["role"] for m in store.conversation]
    assert roles == ["user", "assistant", "tool", "assistant"]
    result = json.loads(store.conversation[2]["content"])
    assert result["results"][0]["source"] == "notes"
    assert result["results"][0]["label"] == "NOTE"

    # The second request carried the tool result and the tool schemas.
    second = agent.client.requests[1]
    assert second["messages"][-1]["role"] == "tool"
    names = {t["function"]["name"] for t in second["tools"]}
    assert {"search_sources", "read_document", "run_python"} <= names


def test_system_prompt_lists_sources(config_with_notes):
    agent, _ = make_agent(config_with_notes, [])
    assert '"name": "notes"' in agent.system_prompt
    assert "EVIDENCE LABELS" in agent.system_prompt


def test_disabled_tool_is_refused_and_hidden(config_with_notes):
    agent, _ = make_agent(
        config_with_notes,
        [reply(tool_calls=[tool_call("c1", "run_python", {"code": "print(1)"})]), reply("ok")],
    )
    store = agent.open_store("p", "s")
    workflow.set_phase(store.project_state, "read-only")
    workflow.set_tools(store.project_state, ["run_python"], enabled=False)

    agent.ask(store, "compute")
    result = json.loads(store.conversation[2]["content"])
    assert result["error"] == "TOOL_DISABLED_IN_PHASE"
    assert "run_python" not in {t["function"]["name"] for t in agent.client.requests[0]["tools"]}


def test_disabled_source_is_refused(config_with_notes):
    agent, _ = make_agent(
        config_with_notes,
        [
            reply(
                tool_calls=[tool_call("c1", "search_sources", {"query": "x", "source": "notes"})]
            ),
            reply("ok"),
        ],
    )
    store = agent.open_store("p", "s")
    workflow.set_phase(store.project_state, "no-notes")
    workflow.set_sources(store.project_state, ["label:NOTE"], enabled=False)
    agent.ask(store, "search")
    assert "disabled" in json.loads(store.conversation[2]["content"])["message"]


def test_invalid_arguments_and_unknown_tool(config):
    bad = tool_call("c1", "list_jobs", {})
    bad.function.arguments = "{not json"
    agent, _ = make_agent(
        config,
        [reply(tool_calls=[bad, tool_call("c2", "no_such_tool", {})]), reply("done")],
    )
    store = agent.open_store("p", "s")
    agent.ask(store, "go")
    results = [json.loads(m["content"]) for m in store.conversation if m["role"] == "tool"]
    assert results[0]["error"] == "Invalid tool arguments"
    assert results[1]["error"].startswith("Unknown tool")


def test_resident_knowledge_and_cards_attached(config_with_notes, tmp_path):
    notes = tmp_path / "core.md"
    notes.write_text("Always report units.")
    config_with_notes.core_notes = notes
    agent, _ = make_agent(config_with_notes, [reply("fine")])

    store = CardStore(config_with_notes.cards_db)
    card = {
        "title": "Carbon annealing",
        "one_line": "Graphitization study",
        "keywords": ["graphitization", "annealing"],
    }
    conn = store._db()
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("notes:1", "notes", "1", "h", json.dumps(card), "m", "t"),
    )
    conn.commit()
    conn.close()

    session = agent.open_store("p", "s")
    agent.ask(session, "carbon graphitization and annealing")
    system = "\n".join(
        m["content"] for m in agent.client.requests[0]["messages"] if m["role"] == "system"
    )
    assert "Always report units." in system
    assert "notes:1 | Carbon annealing" in system
    assert "RELEVANT KNOWLEDGE CARDS" in system


def test_history_digest_for_long_sessions(config):
    config.agent.max_recent_messages = 2
    agent, _ = make_agent(config, [reply("a1"), reply("a2"), reply("a3")])
    store = agent.open_store("p", "s")
    for q in ("q1", "q2", "q3"):
        agent.ask(store, q)
    last = agent.client.requests[-1]["messages"]
    digest = next(m["content"] for m in last if "EARLIER CONVERSATION" in (m["content"] or ""))
    assert "USER: q1" in digest
