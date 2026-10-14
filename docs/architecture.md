# Architecture

```text
src/research_agent/
  cli.py          REPL and subcommands
  agent.py        ResearchAgent: context assembly and the tool-calling loop
  prompts.py      domain-neutral system prompt + per-source guidance
  config.py       TOML configuration
  llm.py          OpenAI-compatible client (key from the environment)
  store.py        projects, sessions, memories (JSON files)
  history.py      recent-message selection, digest, conversation repair
  workflow.py     phases with rules, disabled tools and disabled sources
  jobs.py         detached background jobs and run_python
  terminal.py     multi-line terminal input
  tools/          tool definitions exposed to the model
    core.py         run_python, jobs, save_finding
    data.py         list_sources, search_sources, read_document, list_documents,
                    knowledge-card tools
  sources/        the data-source interface and built-in sources
    base.py         DataSource, SearchHit, Document, ...
    registry.py     built-in names, "module:Class" and entry-point plugins
    fts.py          SQLite FTS5 helpers
    pdf_folder.py, text_folder.py, lammps_docs.py
  knowledge/
    cards.py        knowledge cards (LLM digests), catalog, auto-retrieval
```

## One request

`ResearchAgent.build_messages` assembles, in this order:

1. the **system prompt**: base rules, the list of configured sources, and each
   source's `instructions()`;
2. the **resident knowledge**: core notes, the knowledge-card catalog, and each
   source's `resident_context()`. These first two blocks rarely change, so the
   provider's prefix cache can reuse them across requests;
3. the **persistent context**: project state, findings and memories;
4. **knowledge cards** matched to the latest user message;
5. the **workflow phase** constraints, if any;
6. a **digest** of older messages (user requests and agent answers, no tool output);
7. the **recent messages** in full, with long tool outputs clipped.

`ResearchAgent.ask` then loops: call the model, run the requested tools, append
results, repeat until the model answers without tool calls. The conversation is saved
after every step. If a session was interrupted mid-tool, `repair_conversation` adds
placeholder results, so the history stays valid for the API.

## Safety rules for jobs

- Jobs run in their own process session, so Ctrl+C in the agent never reaches them.
- `job_status` measures CPU use over the waiting window. A job with buffered output
  but busy CPUs is reported as `RUNNING_COMPUTING`, not as stuck.
- The agent's `stop_job` is refused unless the job has been idle (no output, no CPU)
  for 30 minutes. Only the user's `/kill` stops a busy job.
- `run_python` refuses code that kills processes. Code still running after the
  timeout is not killed; it becomes a background job.

## Adding a tool

Add a `Tool(name, description, parameters, handler)` to `tools/core.py` or
`tools/data.py`. The handler receives a `ToolContext` (config, store, sources, cards)
and the parsed arguments, and returns anything JSON-serialisable. Exceptions are
caught and returned to the model as errors.
