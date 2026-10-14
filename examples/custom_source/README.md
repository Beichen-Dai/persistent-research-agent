# Example: connect a SQLite table

`sqlite_table_source.py` is a complete custom data source that lets the agent search
and read the rows of any table in a SQLite database. Use it directly, or as a template
for other databases or web APIs.

## Try it

```bash
# 1. a small database
sqlite3 lab.sqlite "CREATE TABLE experiments (id INTEGER PRIMARY KEY, sample TEXT, summary TEXT);
  INSERT INTO experiments (sample, summary) VALUES
  ('TiC-CDC-800', 'Argon uptake 18 mmol/g at 77 K'),
  ('Cu-film', 'XRD shows fcc peaks after annealing');"

# 2. make the example importable
export PYTHONPATH=/path/to/persistent-research-agent/examples/custom_source
```

```toml
# 3. research-agent.toml
[[sources]]
name = "lab"
type = "sqlite_table_source:SqliteTableSource"
description = "Experiment summaries, one row per sample"
database = "lab.sqlite"
table = "experiments"
id_column = "id"
title_column = "sample"
text_columns = ["summary"]
```

```bash
# 4. check and use it
research-agent sources
research-agent search --source lab "argon uptake"
research-agent        # then ask: "Which sample had the highest argon uptake?"
```

The database is opened read-only, and table and column names are validated before
they are used in SQL.
