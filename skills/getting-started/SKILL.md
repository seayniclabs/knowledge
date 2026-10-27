---
name: getting-started
description: Set up and check the Knowledge MCP bundle (Open Notebook search, reading and writing). Use when the user installs Knowledge, asks what Knowledge can do, gets a "not_configured", "auth_failed" or "unreachable" error from a knowledge tool, or wants to turn the Open Notebook module on or off.
license: MIT
source: https://github.com/seayniclabs/knowledge
author: Seaynic Labs
---

# Getting started with Knowledge

Knowledge connects the assistant to a self-hosted Open Notebook server. The user's notes stay on their server. Knowledge keeps nothing of its own.

## 1. Check what is ready

Call `knowledge_modules` first. Read each module's `enabled`, `configured` and `missing` fields. Report every name in `missing` back to the user, exactly as written. Never guess a value for a setting.

## 2. Settings the user adds

Settings go in the `env` block of the MCP client's config, or in `~/.config/knowledge/config.toml` under `[open_notebook]`. Environment variables win over the file. The user restarts the MCP client after any change.

| Setting | Needed for |
|---|---|
| `KNOWLEDGE_OPEN_NOTEBOOK_URL` | Required. The address of the Open Notebook server, with no path, e.g. `https://notebook.example.com`. |
| `KNOWLEDGE_OPEN_NOTEBOOK_PASSWORD` | Only when the server sets `OPEN_NOTEBOOK_PASSWORD`. Leave it unset otherwise. |
| `KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM` | `true` by default. `false` lets the assistant save links and notes without asking. Local files always need the user's approval. |
| `KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS` | Comma-separated notebook names the assistant may save links and notes to without asking, e.g. `Research,Inbox`. |
| `KNOWLEDGE_OPEN_NOTEBOOK_ENABLED=false` | Turns the module off. |

## 3. Run the first checks

1. In a terminal, run `knowledge-bundle-mcp --doctor`. It prints `ok` or `FIX` for each check and exits 0 when everything is ready. Pass each `FIX` line to the user as written.
2. Call `open_notebook_notebooks` with `limit=5`. It lists the notebooks, most recently updated first, with their `id` values. Every other notebook tool needs one of these ids.
3. Call `open_notebook_search` with a word the user knows appears in one of their notebooks. Confirm that `hits` is not empty.

## 4. Saving to the notebook

`open_notebook_note_create` and `open_notebook_add_source` never save on the first call. Without `confirm=true` they return a preview and save nothing. With `confirm=true`, the user gets an approval prompt in the MCP client. Show the user the preview first. The assistant cannot approve for the user.

- A link or a note is approved by the user, or saved without a prompt when its notebook is in `KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS` or `REQUIRE_CONFIRM` is `false`.
- A local file is always approved by the user. The prompt shows the full path and the size in bytes. Settings never skip that prompt.
- When the client cannot show a prompt, the tool returns `approval_unavailable`. Name the setting that applies. Do not set it yourself.

Every approval, decline and refusal is written to `~/.local/state/knowledge/approvals.jsonl`. It holds titles, notebook names and sizes, never the text itself. The user can read it at any time.

## 5. Errors

Every error reads `knowledge.<module>.<code>: <message> Fix: <hint>`. Pass the `Fix` text to the user as written.

| Code | Meaning |
|---|---|
| `not_configured` | A required setting is missing. Name it. |
| `bad_url` | The URL does not start with `http://` or `https://`, or names no host. |
| `auth_failed` | The server rejected the password (HTTP 401). |
| `unreachable` | The server did not answer. Check the URL and that the server is running. |
| `not_found` | The id does not exist. Look it up with the list or search tools. |
| `approval_unavailable` | The client cannot show an approval prompt, and the notebook is not pre-approved. |
| `blocked_secret` | The text looks like a credential, so nothing was saved. The error names the kind, never the value. |
| `path_refused` | The file name or folder suggests credentials, so Knowledge will not send it. |
