"""Command-line interface: an interactive REPL plus a few batch subcommands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

from research_agent import __version__, workflow
from research_agent.agent import ResearchAgent
from research_agent.config import Config, ConfigError, load_config
from research_agent.history import repair_conversation
from research_agent.jobs import JobManager
from research_agent.knowledge.cards import CardStore
from research_agent.llm import create_client
from research_agent.sources.base import DataSource, SourceError
from research_agent.sources.registry import build_sources
from research_agent.terminal import read_user_input
from research_agent.tools import available_tools

RULE = "=" * 70

HELP = """\
Projects and sessions
  /status                     Project, session and memory overview
  /history [N]                Last N messages (default 10)
  /projects                   List projects
  /project NAME               Switch to (or create) a project
  /sessions                   List sessions of the current project
  /session NAME               Switch to (or create) a session
  /clear-session              Archive and clear the current conversation

Memory and state
  /remember TEXT              Add a user-confirmed global memory
  /memory                     Show all memories
  /state KEY VALUE            Set a project-state value
  /findings                   Show saved findings

Data sources and knowledge
  /sources                    List data sources and their status
  /index [SOURCE] [--force]   Build/update source indexes, then knowledge cards
  /search [SOURCE:]QUERY      Search the data sources directly
  /source NAME ACTION         Run a source-specific action (see /sources)
  /cards [build|rebuild]      Knowledge-card status, or build cards
  /card KEY [KEY ...]         Show knowledge cards (e.g. /card papers:12)
  /core                       Show the knowledge sent with every request

Workflow phases
  /workflow PRESET | clear    Load a preset (e.g. reproduce) or remove the workflow
  /phase [NAME]               Show the workflow, or switch to (or create) phase NAME
  /next                       Move to the next phase
  /rule TEXT                  Add a rule to the current phase
  /rules clear                Remove the rules of the current phase
  /tools on|off NAME ...      Enable/disable tools in the current phase
  /sources-phase on|off NAME  Enable/disable a source (or label:LABEL) in the phase

Background jobs
  /jobs                       List jobs
  /job ID                     Status and log tail of a job
  /kill ID                    Stop a job (only the user can stop running jobs)

Input
  A pasted multi-line prompt is sent as one message. To type one, start and end it
  with a line containing only \"\"\". Ctrl+C while the agent works interrupts it,
  keeps the conversation and leaves background jobs running.

  /help                       This help
  /exit                       Quit
"""


def dump(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def print_tool_call(name: str, arguments: dict[str, Any]) -> None:
    print(f"\n[tool] {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def index_sources(sources: dict[str, DataSource], names: list[str], force: bool) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name in names or list(sources):
        if name not in sources:
            results[name] = {"error": "unknown source"}
            continue
        print(f"\nIndexing '{name}' ...")
        try:
            results[name] = sources[name].index(force=force)
        except Exception as exc:
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return results


def build_cards(
    config: Config, sources: dict[str, DataSource], force: bool = False, client: Any = None
) -> Any:
    store = CardStore(config.cards_db, config.agent.card_keyword_languages)
    client = client or create_client(config.llm)
    return store.build(client, config.llm.card_model or config.llm.model, sources, force=force)


def search_all(sources: dict[str, DataSource], text: str, limit: int = 10) -> list[dict[str, Any]]:
    name, sep, query = text.partition(":")
    selected = {name: sources[name]} if sep and name in sources else sources
    query = query if sep and name in sources else text
    out = []
    for src_name, source in selected.items():
        try:
            for hit in source.search(query, limit=limit):
                hit.source = src_name
                out.append(hit.to_dict())
        except Exception as exc:
            out.append({"source": src_name, "error": f"{type(exc).__name__}: {exc}"})
    return out


def sources_overview(
    sources: dict[str, DataSource], errors: dict[str, str]
) -> list[dict[str, Any]]:
    out = []
    for source in sources.values():
        info = source.describe()
        try:
            info["status"] = source.status()
        except Exception as exc:
            info["status"] = {"error": f"{type(exc).__name__}: {exc}"}
        actions = source.actions()
        if actions:
            info["actions"] = {name: help_text for name, (_, help_text) in actions.items()}
        out.append(info)
    out += [{"name": name, "error": error} for name, error in errors.items()]
    return out


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------


class Repl:
    def __init__(self, config: Config, project: str, session: str) -> None:
        self.config = config
        self.sources, self.source_errors = build_sources(config)
        self.agent = ResearchAgent(config, create_client(config.llm), self.sources, print_tool_call)
        self.store = self.agent.open_store(project, session)
        self.commands: dict[str, Callable[[str], None]] = {
            "/help": lambda _: print(HELP),
            "/status": self.cmd_status,
            "/history": self.cmd_history,
            "/projects": lambda _: print("\n".join(self.store.list_projects()) or "(none)"),
            "/project": self.cmd_project,
            "/newproject": self.cmd_project,
            "/open": self.cmd_project,
            "/sessions": lambda _: print("\n".join(self.store.list_sessions())),
            "/session": self.cmd_session,
            "/newsession": self.cmd_session,
            "/clear-session": self.cmd_clear_session,
            "/remember": self.cmd_remember,
            "/memory": self.cmd_memory,
            "/state": self.cmd_state,
            "/findings": lambda _: dump(self.store.project_state.get("findings", [])),
            "/sources": lambda _: dump(sources_overview(self.sources, self.source_errors)),
            "/index": self.cmd_index,
            "/search": self.cmd_search,
            "/source": self.cmd_source_action,
            "/cards": self.cmd_cards,
            "/card": lambda arg: dump(self.agent.cards.get(arg.split())),
            "/core": self.cmd_core,
            "/workflow": self.cmd_workflow,
            "/phase": self.cmd_phase,
            "/next": self.cmd_next,
            "/rule": self.cmd_rule,
            "/rules": self.cmd_rules,
            "/tools": self.cmd_tools,
            "/sources-phase": self.cmd_sources_phase,
            "/jobs": lambda _: dump(self.jobs().list()),
            "/job": lambda arg: dump(self.jobs().status(arg.strip())),
            "/kill": lambda arg: dump(
                self.jobs().stop(arg.strip(), reason="stopped by user", by_user=True)
            ),
        }

    def jobs(self) -> JobManager:
        return JobManager(self.store.project_dir)

    def banner(self) -> None:
        print(f"\n{RULE}\nPersistent Research Agent {__version__}\n{RULE}")
        print(f"Project : {self.store.project}")
        print(f"Session : {self.store.session}")
        print(f"Model   : {self.config.llm.model}")
        print(f"Sources : {', '.join(self.sources) or '(none)'}")
        for name, error in self.source_errors.items():
            print(f"  ! source '{name}' failed to load: {error}")
        print(f"Turns   : {len(self.store.conversation)}")
        print(f"\nType /help for commands.\n{RULE}\n")

    def run(self) -> None:
        self.banner()
        while True:
            try:
                text = read_user_input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                self.store.save()
                return
            if not text:
                continue
            if text == "/exit":
                self.store.save()
                print("Bye.")
                return
            if text.startswith("/"):
                self.dispatch(text)
                continue
            self.chat(text)

    def dispatch(self, text: str) -> None:
        name, _, arg = text.partition(" ")
        handler = self.commands.get(name)
        if handler is None:
            print(f"Unknown command: {name}. Type /help.")
            return
        try:
            handler(arg.strip())
        except (SourceError, ConfigError) as exc:
            print(f"Error: {exc}")

    def chat(self, text: str) -> None:
        try:
            answer = self.agent.ask(self.store, text)
            print(f"\nAgent:\n{answer}\n")
        except KeyboardInterrupt:
            # Background jobs live in their own process session and keep running.
            self.store.conversation, _ = repair_conversation(self.store.conversation)
            self.store.save()
            print(
                "\n[Agent interrupted. The conversation is kept; background jobs keep running "
                "(see /jobs).]\n"
            )
        except Exception as exc:
            self.store.save()
            print(f"\nAgent error: {type(exc).__name__}: {exc}\n")

    # -- projects and sessions --------------------------------------------------

    def cmd_status(self, _: str) -> None:
        s = self.store
        print(f"Project: {s.project}\nSession: {s.session}\nTurns: {len(s.conversation)}")
        dump(
            {
                "project_state": s.project_state,
                "global_memory": s.global_memory,
                "project_memory": s.project_memory,
                "session_memory": s.session_memory,
            }
        )

    def cmd_history(self, arg: str) -> None:
        n = int(arg) if arg.isdigit() else 10
        for i, msg in enumerate(self.store.conversation[-n:], 1):
            print(f"\n[{i}] {msg.get('role', '?')}")
            if msg.get("content"):
                print(str(msg["content"])[:5000])
            for call in msg.get("tool_calls") or []:
                print("  tool call:", call["function"]["name"])

    def cmd_project(self, arg: str) -> None:
        if not arg:
            print("Usage: /project NAME")
            return
        self.store = self.agent.open_store(arg, self.store.session)
        print(f"Project: {self.store.project}")

    def cmd_session(self, arg: str) -> None:
        if not arg:
            print("Usage: /session NAME")
            return
        self.store = self.agent.open_store(self.store.project, arg)
        print(f"Session: {self.store.session}")

    def cmd_clear_session(self, _: str) -> None:
        archived = self.store.archive_conversation()
        print(f"Conversation cleared{f' (archived to {archived})' if archived else ''}.")

    # -- memory and state ---------------------------------------------------------

    def cmd_remember(self, arg: str) -> None:
        if not arg:
            print("Usage: /remember TEXT")
            return
        self.store.global_memory.append(
            {"type": "user_rule", "content": arg, "source": "user_confirmed"}
        )
        self.store.save()
        print("Memory saved.")

    def cmd_memory(self, _: str) -> None:
        dump(
            {
                "global": self.store.global_memory,
                "project": self.store.project_memory,
                "session": self.store.session_memory,
            }
        )

    def cmd_state(self, arg: str) -> None:
        key, _, value = arg.partition(" ")
        if not key or not value:
            print("Usage: /state KEY VALUE")
            return
        self.store.project_state[key] = value
        self.store.save()
        print(f"Project state updated: {key} = {value}")

    # -- sources and knowledge ----------------------------------------------------

    def cmd_index(self, arg: str) -> None:
        words = arg.split()
        force = "--force" in words
        names = [w for w in words if w != "--force"]
        dump(index_sources(self.sources, names, force))
        print("\nBuilding knowledge cards for new or changed documents ...")
        dump(build_cards(self.config, self.sources, client=self.agent.client))

    def cmd_search(self, arg: str) -> None:
        if not arg:
            print("Usage: /search [SOURCE:]QUERY")
            return
        dump(search_all(self.sources, arg))

    def cmd_source_action(self, arg: str) -> None:
        name, _, action = arg.partition(" ")
        if name not in self.sources:
            print(f"Unknown source: {name}")
            return
        actions = self.sources[name].actions()
        if action.strip() not in actions:
            print(f"Actions of '{name}': {', '.join(actions) or '(none)'}")
            return
        dump(actions[action.strip()][0]())

    def cmd_cards(self, arg: str) -> None:
        if arg in ("build", "rebuild"):
            dump(
                build_cards(
                    self.config, self.sources, force=arg == "rebuild", client=self.agent.client
                )
            )
        dump(self.agent.cards.status(self.sources))

    def cmd_core(self, _: str) -> None:
        text = self.agent.resident_knowledge() or "(empty)"
        print(text[:3000])
        print(f"\n... {len(text)} characters are sent with every request.")

    # -- workflow -------------------------------------------------------------------

    def _save_and_describe(self) -> None:
        self.store.save()
        print(workflow.describe(self.store.project_state))

    def cmd_workflow(self, arg: str) -> None:
        state = self.store.project_state
        if arg == "clear":
            workflow.clear_workflow(state)
            self.store.save()
            print("Workflow cleared.")
        elif workflow.apply_preset(state, arg):
            self._save_and_describe()
        else:
            print(f"Usage: /workflow PRESET | clear   (presets: {', '.join(workflow.PRESETS)})")

    def cmd_phase(self, arg: str) -> None:
        if arg:
            workflow.set_phase(self.store.project_state, arg)
            self.store.save()
        print(workflow.describe(self.store.project_state) or "No workflow phase set.")

    def cmd_next(self, _: str) -> None:
        if workflow.next_phase(self.store.project_state):
            self._save_and_describe()
        else:
            print("No next phase.")

    def cmd_rule(self, arg: str) -> None:
        if workflow.add_rule(self.store.project_state, arg):
            self._save_and_describe()
        else:
            print("Set a phase first: /phase NAME")

    def cmd_rules(self, arg: str) -> None:
        if arg != "clear":
            print("Usage: /rules clear")
        elif workflow.clear_rules(self.store.project_state):
            self.store.save()
            print("Rules of the current phase cleared.")
        else:
            print("No current phase.")

    def cmd_tools(self, arg: str) -> None:
        mode, *names = arg.split()
        known = {t.name for t in available_tools(self.agent.context(self.store))}
        unknown = [n for n in names if n not in known]
        if mode not in ("on", "off") or not names:
            print("Usage: /tools on|off NAME [NAME ...]")
        elif unknown:
            print(f"Unknown tools: {', '.join(unknown)}\nKnown: {', '.join(sorted(known))}")
        elif workflow.set_tools(self.store.project_state, names, enabled=mode == "on"):
            self._save_and_describe()
        else:
            print("Set a phase first: /phase NAME")

    def cmd_sources_phase(self, arg: str) -> None:
        mode, *names = arg.split()
        if mode not in ("on", "off") or not names:
            print("Usage: /sources-phase on|off NAME|label:LABEL ...")
        elif workflow.set_sources(self.store.project_state, names, enabled=mode == "on"):
            self._save_and_describe()
        else:
            print("Set a phase first: /phase NAME")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def cmd_init(directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, target in (
        ("config.example.toml", "research-agent.toml"),
        ("core_notes.example.md", "core_notes.md"),
    ):
        path = directory / target
        if path.exists():
            print(f"Skipped (exists): {path}")
            continue
        path.write_text(resources.files("research_agent.data").joinpath(name).read_text(), "utf-8")
        written.append(str(path))
    for path in written:
        print(f"Created {path}")
    print("\nNext: edit research-agent.toml, export your API key, then run `research-agent`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-agent", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-c", "--config", help="Path to research-agent.toml")
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="Interactive session (default)")
    for p in (parser, chat):
        p.add_argument("-p", "--project", default="default")
        p.add_argument("-s", "--session", default="main")

    init = sub.add_parser("init", help="Write an example configuration")
    init.add_argument("directory", nargs="?", default=".")

    sub.add_parser("sources", help="List data sources and their status")

    index = sub.add_parser("index", help="Build/update source indexes")
    index.add_argument("names", nargs="*", help="Sources to index (default: all)")
    index.add_argument("--force", action="store_true", help="Rebuild unchanged documents too")
    index.add_argument(
        "--cards", action="store_true", help="Also build knowledge cards (uses the API)"
    )

    search = sub.add_parser("search", help="Search the data sources")
    search.add_argument("query", nargs="+")
    search.add_argument("--source")
    search.add_argument("--limit", type=int, default=10)

    args = parser.parse_args(argv)

    if args.command == "init":
        return cmd_init(Path(args.directory))

    try:
        config = load_config(args.config)
        if args.command == "sources":
            dump(sources_overview(*build_sources(config)))
        elif args.command == "index":
            sources, errors = build_sources(config)
            for name, error in errors.items():
                print(f"Source '{name}' failed to load: {error}", file=sys.stderr)
            dump(index_sources(sources, args.names, args.force))
            if args.cards:
                dump(build_cards(config, sources))
        elif args.command == "search":
            sources, _ = build_sources(config)
            text = " ".join(args.query)
            dump(search_all(sources, f"{args.source}:{text}" if args.source else text, args.limit))
        else:
            Repl(config, args.project, args.session).run()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
