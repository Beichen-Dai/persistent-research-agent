# Configuration reference

The agent reads `research-agent.toml`. Lookup order:

1. `research-agent --config PATH`
2. the `RESEARCH_AGENT_CONFIG` environment variable
3. `./research-agent.toml`

Without a file, built-in defaults are used (no data sources). Relative paths are
resolved against the directory of the configuration file. `research-agent init`
writes a commented example.

## `[paths]`

| Key | Default | Meaning |
|---|---|---|
| `data_dir` | `agent-data` | Projects, sessions, source indexes and knowledge cards |
| `workspace` | `workspace` | Working directory of `run_python` |
| `core_notes` | (none) | Markdown file sent with every request (highest priority) |

## `[llm]`

| Key | Default | Meaning |
|---|---|---|
| `base_url` | `https://api.deepseek.com` | Any OpenAI-compatible endpoint |
| `model` | `deepseek-v4-flash` | Chat model with tool calling |
| `api_key_env` | `DEEPSEEK_API_KEY` | **Name** of the environment variable that holds the key |
| `card_model` | same as `model` | Model used to build knowledge cards |

The API key itself must never be written into the file; a file containing
`api_key` is rejected.

Other providers work as long as they implement chat completions with tool calling,
for example:

```toml
[llm]
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"
```

## `[agent]`

| Key | Default | Meaning |
|---|---|---|
| `max_recent_messages` | 40 | Messages sent in full; older ones are condensed into a digest |
| `max_digest_chars` | 30000 | Size limit of that digest |
| `max_tool_chars` | 20000 | Tool outputs sent back to the model are clipped to this size |
| `run_python_timeout` | 120 | Seconds after which `run_python` continues as a background job |
| `auto_cards` | 3 | Knowledge cards attached automatically to each message (0 = off) |
| `card_keyword_languages` | `[]` | Extra keyword languages in cards, e.g. `["Chinese"]`, so questions in that language match English documents |

## `[[sources]]`

One table per data source:

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Unique name used by the agent and in commands |
| `type` | yes | Built-in type, `module:Class`, or plugin name ([data-sources.md](data-sources.md)) |
| `description` | no | Tells the model what the source holds |
| `enabled` | no | `false` keeps the entry but does not load it |
| *other keys* | | Passed to the source class |

### `pdf_folder`

| Option | Meaning |
|---|---|
| `path` | Folder searched recursively for `*.pdf` (e.g. Zotero's `storage`) |

Search filter: `{"mode": "title"}` searches file names and titles instead of the
full text, and works before indexing.

### `text_folder`

| Option | Default | Meaning |
|---|---|---|
| `path` | | Folder searched recursively |
| `patterns` | `["*.md", "*.txt"]` | File patterns to index |
| `cards` | `false` | Build knowledge cards for these files |

### `lammps_docs`

| Option | Meaning |
|---|---|
| `path` | LAMMPS source tree (needs `doc/src` and `examples`) |
| `version` | Label shown to the model (default: folder name) |
| `script_dirs` | Folders with your input scripts, used by `/source NAME cheatsheet` |

Search filters: `{"doc_type": "doc" | "example"}`, `{"category": "PACKAGES"}`.

## Files on disk

```text
<data_dir>/
  projects/
    global_memory.json
    <project>/
      project_state.json, project_memory.json
      jobs/jobs.json, jobs/<job_id>/{run.sh,output.log,exit_code}
      sessions/<session>/{conversation.json,session_memory.json,archive/}
  sources/<name>/            index and cache of each source
  knowledge/cards.db         knowledge cards
```
