# Changelog

Knowledge follows [semantic versioning](https://semver.org). Modules added after 0.1 ship switched off.

## 0.1.1 — 2026-10-27

- Fix: the command is now `knowledge-bundle-mcp`, matching the package name, so `uvx knowledge-bundle-mcp` works. `knowledge-mcp` still works too.

## 0.1.0 — unreleased

- First release as one bundle, with one module: `open_notebook` (Open Notebook over its REST API).
- `knowledge_modules` discovery tool: which modules are on, which settings are missing, what each tool does.
- Tools: list notebooks, search (keyword or vector, optionally one notebook), list a notebook's notes, read a note, read a source, create a note.
- Paged results with `offset` and `next_offset`; every tool returns structured data with a published output schema.
- `open_notebook_note_create` previews by default. Saving needs the person's approval: asked through MCP elicitation, or given ahead of time with `KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS` or `KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM=false`. The assistant's own `confirm=true` is never enough.
- Text that looks like a credential is refused before anything is written; the error names the kind, never the value.
- Optional server password (Bearer), read from settings only.
- Runs over stdio or streamable HTTP. Starts with no settings and reports what is missing when a tool is called.

MIT License

Copyright (c) 2026 Seaynic Labs
