import pytest

from research_agent import workflow
from research_agent.store import AgentStore, safe_name


def test_store_round_trip(tmp_path):
    store = AgentStore(tmp_path, "proj", "s1")
    store.conversation.append({"role": "user", "content": "hi"})
    store.global_memory.append({"content": "rule"})
    store.save()

    again = AgentStore(tmp_path, "proj", "s1")
    assert again.conversation == [{"role": "user", "content": "hi"}]
    assert again.global_memory == [{"content": "rule"}]
    assert AgentStore(tmp_path, "other", "s1").global_memory == [{"content": "rule"}]
    assert again.list_projects() == ["other", "proj"]


def test_archive_conversation(tmp_path):
    store = AgentStore(tmp_path, "p", "s")
    store.conversation.append({"role": "user", "content": "hi"})
    archived = store.archive_conversation()
    assert archived is not None and archived.exists()
    assert store.conversation == []


def test_safe_name():
    assert safe_name(" my project/1 ") == "my_project_1"
    with pytest.raises(ValueError):
        safe_name("  ")


def test_preset_phases_and_constraints():
    state: dict = {}
    assert workflow.apply_preset(state, "reproduce")
    assert workflow.current_phase(state)[0] == "literature"
    assert "start_job" in workflow.disabled_tools(state)

    assert workflow.next_phase(state) == "simulate"
    assert workflow.source_disabled(state, "papers", "PAPER")
    assert not workflow.source_disabled(state, "lammps", "DOC")
    assert "ACTIVE WORKFLOW PHASE" in workflow.describe(state)


def test_custom_phase_rules_and_tools():
    state: dict = {}
    assert not workflow.add_rule(state, "x")  # no phase yet
    workflow.set_phase(state, "analysis")
    assert workflow.add_rule(state, "be careful")
    assert workflow.set_tools(state, ["run_python"], enabled=False)
    assert workflow.disabled_tools(state) == {"run_python"}
    assert workflow.set_sources(state, ["notes"], enabled=False)
    assert workflow.source_disabled(state, "notes", "NOTE")
    assert workflow.clear_rules(state)
    workflow.clear_workflow(state)
    assert workflow.describe(state) is None


def test_findings_replace_same_key():
    state: dict = {}
    workflow.add_finding(state, "T", "300 K", "PAPER", citation="p. 2")
    workflow.add_finding(state, "T", "310 K", "LOCAL")
    assert [f["value"] for f in state["findings"]] == ["310 K"]
