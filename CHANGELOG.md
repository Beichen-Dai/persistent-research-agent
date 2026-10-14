# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-14

### Added

- Interactive research agent for OpenAI-compatible chat APIs (DeepSeek by default).
- Persistent projects, sessions, global/project/session memories and saved findings.
- History digest for long sessions and repair of interrupted tool-call chains.
- Pluggable data-source interface (`DataSource`) with built-in `pdf_folder`,
  `text_folder` and `lammps_docs` sources, plus `module:Class` and entry-point plugins.
- Generic source tools: `list_sources`, `search_sources`, `read_document`,
  `list_documents`.
- Knowledge cards with catalog, automatic retrieval and related-document links.
- `run_python` and detached background jobs with progress-aware monitoring.
- Workflow phases with rules, disabled tools and disabled sources.
- Commands `init`, `sources`, `index` and `search`, and an example SQLite source.
