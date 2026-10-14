# Connecting your own data sources

A **data source** is anything the agent can search for evidence: a folder of PDFs, a
lab notebook, a manual, a SQL database, a REST API. All sources share one interface,
`research_agent.sources.DataSource`, and the agent reaches every source through the
same tools:

| Tool             | Calls                                          |
|------------------|------------------------------------------------|
| `list_sources`   | `describe()` of every source                   |
| `search_sources` | `search()` of one source or of all of them     |
| `read_document`  | `read()`                                       |
| `list_documents` | `list_documents()`                             |

So a new source needs **no change to the agent**: write a class, add it to the
configuration, and the model can use it.

## 1. Write the class

Only `search` is required. Everything else is optional.

```python
from pathlib import Path
from typing import Any

from research_agent.sources import DataSource, Document, SearchHit, SourceError


class ElnSource(DataSource):
    """Entries of an electronic lab notebook behind a REST API."""

    evidence_label = "ELN"  # how the agent cites this source
    filters_help = '{"project": "<project code>"}'  # shown to the model

    def __init__(self, name: str, data_dir: Path, description: str = "", **options: Any):
        super().__init__(name, data_dir, description or "Lab notebook entries", **options)
        if "url" not in options:
            raise SourceError(f"Source '{name}': the option 'url' is required.")
        self.client = ElnClient(options["url"], token_env=options.get("token_env"))

    def search(self, query: str, limit: int = 10, filters: dict[str, Any] | None = None):
        entries = self.client.search(query, project=(filters or {}).get("project"), limit=limit)
        return [
            SearchHit(
                doc_id=e.id,
                title=e.title,
                text=e.snippet,  # the evidence the model will read
                location=e.date,  # page, section, date, row key, ...
                score=e.score,
                metadata={"author": e.author},
            )
            for e in entries
        ]

    def read(self, doc_id: str, max_chars: int = 30_000) -> Document:
        entry = self.client.get(doc_id)
        return Document(
            doc_id, entry.title, entry.body[:max_chars], truncated=len(entry.body) > max_chars
        )
```

### The interface

| Method | Required | Purpose |
|---|---|---|
| `search(query, limit, filters)` | **yes** | Return `SearchHit`s, best first |
| `read(doc_id, max_chars)` | no | Full content of one document |
| `list_documents(limit, offset)` | no | Browse the source |
| `index(force)` | no | Build or update a search index (`/index`) |
| `status()` | no | Statistics shown by `/sources` |
| `instructions()` | no | Extra guidance added to the system prompt |
| `resident_context()` | no | Short, stable text sent with every request |
| `actions()` | no | Extra commands run with `/source NAME ACTION` |
| `card_candidates()` / `card_text()` | no | Opt in to knowledge cards |

Raise `SourceError` for bad requests (unknown id, missing file); the agent passes the
message to the model. A source that raises any other exception during
`search_sources` is skipped and reported, so one broken backend never stops the
agent.

### Constructor arguments

`__init__(name, data_dir, description="", **options)`:

- `name`: the name from the configuration;
- `data_dir`: a private directory for indexes or caches
  (`<data_dir>/sources/<name>`);
- `description`: shown to the model, so it knows when to use the source;
- `options`: every other key of the `[[sources]]` entry. Keys named `path` or
  `root`, keys ending in `_path` or `_dir`, and lists in keys ending in `_dirs` are
  resolved relative to the configuration file.

## 2. Register it

```toml
[[sources]]
name = "eln"
type = "my_lab_tools.eln:ElnSource"
description = "Electronic lab notebook, 2019 onwards"
url = "https://eln.example.org/api"
token_env = "ELN_TOKEN"            # read secrets from the environment, not the file
```

`type` can be:

1. a built-in type: `pdf_folder`, `text_folder`, `lammps_docs`;
2. an import path, `"package.module:ClassName"` (the module must be importable, e.g.
   installed or on `PYTHONPATH`);
3. a plugin name registered under the entry-point group `research_agent.sources`:

   ```toml
   # pyproject.toml of your plugin package
   [project.entry-points."research_agent.sources"]
   eln = "my_lab_tools.eln:ElnSource"
   ```

   and then `type = "eln"`.

Check that it loads:

```bash
research-agent sources
research-agent search --source eln "XRD copper"
```

## 3. Optional: knowledge cards

Implement `card_candidates()` (yield a `CardCandidate(doc_id, title, content_hash)`
for every document that should get a card) and `card_text(doc_id, max_chars)` (the
text to digest; markers like `[p.5]` let the card cite pages). Cards are built by
`/index`, `/cards build` or `research-agent index --cards`, and are rebuilt only when
`content_hash` changes.

## Tips

- Keep `SearchHit.text` short and self-contained: it is what the model reads. Put
  long content behind `read()`.
- Give each source a precise `description`; the model uses it to choose sources.
- Use a distinct `evidence_label` so answers say exactly where a statement came from.
- Open databases read-only. The SQLite example uses `mode=ro`.
- Validate identifiers before you put them into SQL. The example rejects anything
  that is not a plain identifier.

A complete, tested example is in
[`examples/custom_source/sqlite_table_source.py`](../examples/custom_source/sqlite_table_source.py).
