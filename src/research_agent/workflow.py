"""Phase-based constraints for a research workflow.

A project can have an ordered list of phases (e.g. literature -> simulate ->
compare). Each phase carries its own rules, disabled tools and disabled data
sources. Rules are shown to the model every turn; disabled tools are removed
from the tool list and refused if called anyway.

A disabled-source entry is either a source name (``"papers"``) or an
evidence label prefixed with ``label:`` (``"label:PAPER"`` disables every
source whose evidence label is PAPER).

Stored in ``project_state["workflow"]``::

    {
        "order": ["literature", "simulate", "compare"],
        "current": "literature",
        "phases": {
            "literature": {"rules": [...], "disabled_tools": [...],
                           "disabled_sources": [...]},
            ...
        }
    }

Findings (``save_finding``) are stored in ``project_state["findings"]``.
"""

from __future__ import annotations

from typing import Any

from research_agent.store import now_iso

State = dict[str, Any]

JOB_TOOLS = ["start_job"]

PRESETS: dict[str, dict[str, dict[str, list[str]]]] = {
    # Reproduce a published computation: read once, then compute, then
    # compare against what was extracted at the start.
    "reproduce": {
        "literature": {
            "rules": [
                "Read the target paper(s) now. Extract the complete protocol and every "
                "target value to reproduce.",
                "Save each parameter and target value with save_finding, with its page "
                "number. Later phases will NOT read papers again, so be complete.",
                "Do not launch long computations in this phase.",
            ],
            "disabled_tools": list(JOB_TOOLS),
            "disabled_sources": [],
        },
        "simulate": {
            "rules": [
                "Do not read the literature in this phase. Use only the findings saved "
                "in the literature phase.",
                "Run short tests first, then production runs with start_job.",
                "A job that produces output or uses CPU must never be stopped or "
                "relaunched. Monitor it with job_status until it finishes, then continue.",
            ],
            "disabled_tools": [],
            "disabled_sources": ["label:PAPER"],
        },
        "compare": {
            "rules": [
                "Compare local results with the saved findings, value by value, and state "
                "the agreement or deviation.",
                "Literature sources may only be used to re-check a specific saved value "
                "(cite the page), not to explore new papers.",
                "Do not start new production runs unless the user asks.",
            ],
            "disabled_tools": list(JOB_TOOLS),
            "disabled_sources": [],
        },
    },
}


def _empty_phase() -> dict[str, list[str]]:
    return {"rules": [], "disabled_tools": [], "disabled_sources": []}


def get_workflow(state: State) -> dict[str, Any]:
    wf = state.setdefault("workflow", {})
    wf.setdefault("order", [])
    wf.setdefault("phases", {})
    wf.setdefault("current", None)
    return wf


def current_phase(state: State) -> tuple[str | None, dict[str, list[str]]]:
    wf = get_workflow(state)
    name = wf["current"]
    if not name:
        return None, _empty_phase()
    phase = wf["phases"].setdefault(name, _empty_phase())
    phase.setdefault("disabled_sources", [])
    return name, phase


def set_phase(state: State, name: str) -> dict[str, list[str]]:
    wf = get_workflow(state)
    if name not in wf["phases"]:
        wf["phases"][name] = _empty_phase()
        wf["order"].append(name)
    wf["current"] = name
    return wf["phases"][name]


def next_phase(state: State) -> str | None:
    wf = get_workflow(state)
    if wf["current"] not in wf["order"]:
        return None
    i = wf["order"].index(wf["current"])
    if i + 1 >= len(wf["order"]):
        return None
    wf["current"] = wf["order"][i + 1]
    return wf["current"]


def add_rule(state: State, text: str) -> bool:
    name, phase = current_phase(state)
    if name is None:
        return False
    phase["rules"].append(text)
    return True


def clear_rules(state: State) -> bool:
    name, phase = current_phase(state)
    if name is None:
        return False
    phase["rules"] = []
    return True


def _toggle(state: State, key: str, names: list[str], enabled: bool) -> bool:
    phase_name, phase = current_phase(state)
    if phase_name is None:
        return False
    current = set(phase.get(key, []))
    current = current - set(names) if enabled else current | set(names)
    phase[key] = sorted(current)
    return True


def set_tools(state: State, names: list[str], enabled: bool) -> bool:
    return _toggle(state, "disabled_tools", names, enabled)


def set_sources(state: State, names: list[str], enabled: bool) -> bool:
    return _toggle(state, "disabled_sources", names, enabled)


def apply_preset(state: State, preset: str) -> bool:
    if preset not in PRESETS:
        return False
    phases = PRESETS[preset]
    state["workflow"] = {
        "order": list(phases),
        "current": next(iter(phases)),
        "phases": {name: {k: list(v) for k, v in cfg.items()} for name, cfg in phases.items()},
        "preset": preset,
    }
    return True


def clear_workflow(state: State) -> None:
    state.pop("workflow", None)


def disabled_tools(state: State) -> set[str]:
    return set(current_phase(state)[1]["disabled_tools"])


def source_disabled(state: State, source_name: str, evidence_label: str) -> bool:
    entries = set(current_phase(state)[1].get("disabled_sources", []))
    return source_name in entries or f"label:{evidence_label}" in entries


def describe(state: State) -> str | None:
    """Text block injected into the model context every turn."""
    wf = get_workflow(state)
    name, phase = current_phase(state)
    if name is None:
        return None

    lines = [
        "ACTIVE WORKFLOW PHASE (user-set constraints, MUST be followed):",
        f"Phases: {' -> '.join(wf['order'])}",
        f"Current phase: {name}",
    ]
    if phase["rules"]:
        lines.append("Rules for this phase:")
        lines += [f"  {i}. {rule}" for i, rule in enumerate(phase["rules"], 1)]
    if phase["disabled_tools"]:
        lines.append("Tools disabled in this phase: " + ", ".join(phase["disabled_tools"]))
    if phase.get("disabled_sources"):
        lines.append("Data sources disabled in this phase: " + ", ".join(phase["disabled_sources"]))
    lines.append(
        "Only the user changes the phase. When this phase's goal is done, say so and "
        "ask the user to move to the next phase."
    )
    return "\n".join(lines)


def add_finding(
    state: State, key: str, value: str, source: str, citation: str = "", note: str = ""
) -> dict[str, Any]:
    findings = state.setdefault("findings", [])
    item = {
        "key": key,
        "value": value,
        "source": source,
        "citation": citation,
        "note": note,
        "phase": get_workflow(state)["current"],
        "added": now_iso(),
    }
    # Saving the same key again replaces the earlier value.
    findings[:] = [f for f in findings if f.get("key") != key]
    findings.append(item)
    return item
