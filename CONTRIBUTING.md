# Contributing

Thank you for helping improve Persistent Research Agent.

## Setup

```bash
git clone https://github.com/Beichen-Dai/persistent-research-agent.git
cd persistent-research-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before you open a pull request

```bash
ruff check .            # lint
ruff format .           # format
pytest                  # tests (no API key or network needed)
```

- Keep the code and all text in English.
- Add or update tests for behaviour you change. The tests use a scripted fake LLM
  client (`tests/conftest.py`), so they never call a real API.
- Never commit API keys, personal paths or private data. Configuration files that hold
  real paths belong in your own working directory, not in the repository.
- Update `CHANGELOG.md` and the docs in `docs/` when you change behaviour or commands.

## Adding a data source

New built-in sources live in `src/research_agent/sources/`. Register the class in
`sources/registry.py`, document its options in `docs/configuration.md`, and add tests.
Sources that depend on extra packages or services are usually better published as
separate plugins (see `docs/data-sources.md`).

## Reporting bugs

Please include the command you ran, the relevant part of the output, your Python
version and operating system, and, where possible, a minimal configuration that
reproduces the problem. Remove keys and private paths first.
