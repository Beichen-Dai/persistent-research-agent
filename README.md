# Persistent Research Agent

A low-cost, persistent, tool-using research assistant for the terminal. It works with
any OpenAI-compatible chat API (DeepSeek by default, which keeps running costs low),
remembers your projects across sessions, searches **your own data sources**, runs
Python and long background jobs, and keeps evidence apart from inference.

```text
You: What sp fraction did we get for sample CDC-7?

[tool] search_sources {"query": "CDC-7 sp fraction"}
[tool] read_document  {"source": "notes", "doc_id": "1"}

Agent:
Sample CDC-7 had an sp fraction of 12 % after annealing at 1500 K.
[NOTE] Annealing log (log.md): "Sample CDC-7 was annealed at 1500 K for 0.2 ns; the sp
fraction was 12 percent."
```

## Features

- **Pluggable data sources.** Connect PDF libraries (e.g. Zotero storage), folders of
  notes, software manuals or your own database through one small interface
  ([docs/data-sources.md](docs/data-sources.md)). The agent reaches all of them through
  the same generic tools, so adding a source needs no change to the agent.
- **Evidence labels.** Answers separate source evidence (`[PAPER]`, `[DOC]`, `[NOTE]`,
  `[DATA]`, ...) from local results (`[LOCAL]`) and from the model's own reasoning
  (`[INFERENCE]`).
- **Persistent projects and sessions.** Conversations, memories, project state and
  saved findings live in plain JSON files. Older messages are condensed into a digest,
  so long projects keep their context at a bounded cost.
- **Knowledge cards.** Each document can be digested once into a structured card
  (methods, parameters, findings, mechanisms, with page numbers). A one-line catalog
  is sent with every request and relevant cards are attached automatically.
- **Python and background jobs.** `run_python` for quick analysis; `start_job` for
  simulations that run for hours. Jobs survive agent restarts, and the agent is not
  allowed to stop a job that is still making progress.
- **Workflow phases.** Optional per-project phases (e.g. *literature → simulate →
  compare*) with their own rules, disabled tools and disabled sources.
- **Cache-friendly prompts.** Stable context is placed first so the API's prefix cache
  can reuse it, which keeps repeated requests cheap.

## Requirements

- Python 3.10 or newer
- Linux (WSL2 works). Background-job monitoring reads `/proc`.
- An API key for an OpenAI-compatible endpoint (e.g. [DeepSeek](https://platform.deepseek.com))

## Installation

```bash
git clone https://github.com/Beichen-Dai/persistent-research-agent.git
cd persistent-research-agent
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quick start

```bash
mkdir my-research && cd my-research
research-agent init                 # writes research-agent.toml and core_notes.md
# edit research-agent.toml: point the data sources at your folders

export DEEPSEEK_API_KEY=...         # never put the key in a file you commit
research-agent index                # build the search indexes
research-agent                      # start an interactive session
```

Useful commands inside a session: `/help`, `/sources`, `/search QUERY`, `/index`,
`/jobs`, `/project NAME`, `/exit`. See [docs/commands.md](docs/commands.md).

## Configuration

Everything is configured in `research-agent.toml`
([full reference](docs/configuration.md)):

```toml
[llm]
base_url = "https://api.deepseek.com"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"     # the NAME of the variable, not the key

[[sources]]
name = "papers"
type = "pdf_folder"
path = "~/Zotero/storage"
description = "My reference library"
```

## Connecting your own data

Subclass `DataSource`, implement `search`, and register the class:

```python
from research_agent.sources import DataSource, SearchHit


class MyDatabaseSource(DataSource):
    evidence_label = "DATA"

    def search(self, query, limit=10, filters=None):
        rows = my_query(query, limit)
        return [SearchHit(doc_id=str(r.id), title=r.name, text=r.summary) for r in rows]
```

```toml
[[sources]]
name = "lab"
type = "my_package.sources:MyDatabaseSource"
description = "Measured isotherms, one row per experiment"
dsn = "postgresql://..."            # extra keys are passed to __init__
```

A complete, tested example for SQLite tables is in
[examples/custom_source](examples/custom_source/sqlite_table_source.py), and the
walkthrough is in [docs/data-sources.md](docs/data-sources.md).

Built-in source types:

| Type          | Holds                                    | Label   |
|---------------|------------------------------------------|---------|
| `pdf_folder`  | A folder of PDFs (e.g. Zotero `storage`) | `PAPER` |
| `text_folder` | Markdown / text notes                    | `NOTE`  |
| `lammps_docs` | LAMMPS documentation and examples        | `DOC`   |

## Security notes

- The agent can run Python code and shell commands on your machine (`run_python`,
  `start_job`). Run it under an account and in a directory you are comfortable with.
- The API key is only read from an environment variable. A configuration file that
  contains `api_key` is rejected.
- Everything you send to the model goes to the configured API provider.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md).

## License

[MIT](LICENSE)
