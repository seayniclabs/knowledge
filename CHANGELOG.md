# Changelog

Knowledge follows [semantic versioning](https://semver.org). Modules added after 0.1 ship switched off.

## 0.2.0 — 2026-10-27

- **Skills.** Six skills ship in the package and as MCP prompts (one per skill) with their files as resources: `getting-started`, `weekly-review`, `research-with-sources`, `meeting-notes`, `decision-record`, and the credited third-party skills `icm-architect` and `obsidian-markdown` (MIT, with their license files and pinned sources in `CREDITS.md`).
- **New tool: `open_notebook_add_source`.** Adds a web link, or a local file you name, as a source in a notebook. Preview first; you approve each one. A local file always needs your approval, with its full path and size shown, and is never pre-approved in settings. Text that looks like a credential is refused, and files named like credential stores are refused before anything is read for sending. Sources are processed in the background; check progress with `open_notebook_source_get`.
- **Approval log.** Every approval asked, granted, declined or refused is appended to `~/.local/state/knowledge/approvals.jsonl` (set `KNOWLEDGE_APPROVAL_LOG` to move it). Bodies and file contents are never logged.
- **`knowledge-bundle-mcp --doctor`.** Checks the settings, the Open Notebook address, the password, and makes one read-only call to list notebooks. Prints a `FIX` line for each problem and exits 1 if anything needs fixing.
- **Discovery.** `knowledge_modules` now lists the skills, the publisher and the homepage.
- **Trust.** `SECURITY.md`, a security card in the README generated from the live server, a tool-description lock (`tool-manifest.lock.json`), and CI checks for skills, the lock and the card.
- **Packaging.** The Claude Code plugin manifest (`.claude-plugin/plugin.json`) and `.mcp.json` pin `knowledge-bundle-mcp==0.2.0`.
- Verified against Open Notebook's source: source creation is `POST /api/sources` (form fields; a local file is sent as an upload), with `async_processing` returning a `command_id`.

## 0.1.1 — 2026-10-27

- Fix: the command is now `knowledge-bundle-mcp`, matching the package name, so `uvx knowledge-bundle-mcp` works. `knowledge-mcp` still works too.

## 0.1.0 — 2026-10-27 (on PyPI; its command name did not match the package, use 0.1.1)

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
