# Knowledge

<!-- mcp-name: io.github.seayniclabs/knowledge -->

Knowledge is an MCP server for people who run their own knowledge base. It lets an AI assistant search, read and write the notes in it, from one install.

It is a **bundle**: one server made of modules. You switch on the modules you use. New modules arrive switched off, so an update never changes what your assistant can do until you choose to.

**Audience:** people who self-host [Open Notebook](https://github.com/lfnovo/open-notebook) and want their assistant to use it.

| Module | What it does | Status |
|---|---|---|
| `open_notebook` | Open Notebook over its REST API: list notebooks, search (keyword or meaning), read notes and sources, create a note (you approve each one) | 0.1 |

Knowledge works with any MCP client: Claude Desktop, Claude Code, Cursor, VS Code, Sonique, Hermes, OpenClaw and others. It runs on your computer and stores nothing. Your notes stay in your Open Notebook server.

## Install

Knowledge needs Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/) you don't install anything ahead of time:

```bash
uvx knowledge-bundle-mcp --version
```

Or with pip:

```bash
pip install knowledge-bundle-mcp
```

## Settings

Set these as environment variables in your MCP client's config, or put them in a config file (see below). Knowledge starts without any of them and tells you what's missing when you call a tool.

| Variable | Module | What it is |
|---|---|---|
| `KNOWLEDGE_OPEN_NOTEBOOK_URL` | open_notebook | Address of your Open Notebook server, e.g. `https://notebook.example.com`. No path. |
| `KNOWLEDGE_OPEN_NOTEBOOK_PASSWORD` | open_notebook | Only if your server has a password (`OPEN_NOTEBOOK_PASSWORD` on the server). Leave it unset otherwise. |
| `KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM` | open_notebook | `true` by default: you approve each new note. `false` lets the assistant create notes on its own. |
| `KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS` | open_notebook | Optional. Comma-separated notebook names the assistant may write to without asking, e.g. `Research,Inbox` |
| `KNOWLEDGE_<MODULE>_ENABLED` | any | `false` turns a module off, e.g. `KNOWLEDGE_OPEN_NOTEBOOK_ENABLED=false` |

**Config file (optional).** Knowledge also reads `~/.config/knowledge/config.toml`, or the file named by `KNOWLEDGE_CONFIG`. Environment variables win over the file.

```toml
[open_notebook]
url = "https://notebook.example.com"
password = "change-me"
require_confirm = true
```

## Add it to your MCP client

Most clients use the same JSON shape. Put it where your client keeps MCP servers:

```json
{
  "mcpServers": {
    "knowledge": {
      "command": "uvx",
      "args": ["knowledge-bundle-mcp"],
      "env": {
        "KNOWLEDGE_OPEN_NOTEBOOK_URL": "https://notebook.example.com"
      }
    }
  }
}
```

| Client | Where the config goes |
|---|---|
| Claude Desktop | `claude_desktop_config.json` (Settings → Developer → Edit Config) |
| Claude Code | `claude mcp add knowledge -- uvx knowledge-bundle-mcp`, then add the `env` values, or a project `.mcp.json` |
| Cursor | `~/.cursor/mcp.json`, or `.cursor/mcp.json` in a project |
| VS Code | `.vscode/mcp.json`, using `"servers"` instead of `"mcpServers"` |
| Sonique, Hermes, OpenClaw, others | Any client that launches a stdio MCP server: command `uvx`, args `["knowledge-bundle-mcp"]`, plus the env values |

**Over HTTP.** For clients that connect to a URL instead of launching a process:

```bash
knowledge-bundle-mcp --transport http --port 8765
```

It serves streamable HTTP at `http://127.0.0.1:8765/mcp`. It listens only on your own machine by default. Put authentication in front of it before exposing it anywhere else.

## Tools

| Tool | What it returns |
|---|---|
| `knowledge_modules` | Which modules are on, which settings are missing, and what each tool does. Call this first. |
| `open_notebook_notebooks` | Your notebooks, most recently updated first, paged |
| `open_notebook_search` | Matching sources and notes. `mode` is `text` (keywords) or `vector` (meaning; needs an embedding model on the server). Optional `notebook_id`. Paged. |
| `open_notebook_notes` | The notes in one notebook, titles only, paged |
| `open_notebook_note_get` | One note's content, cut at `max_chars` |
| `open_notebook_source_get` | One source: title, topics, extracted text, notebooks |
| `open_notebook_note_create` | Saves a note after you approve it. Without `confirm=true` it only returns a preview. |

Every tool returns structured data with a published schema, so an assistant can work with the results instead of parsing text. Errors always look like `knowledge.<module>.<code>: <message> Fix: <hint>`.

## Safety

- Knowledge only calls the Open Notebook server you configure. It sends nothing anywhere else and keeps no data of its own.
- `open_notebook_note_create` changes your notes, so **you** approve each one. When the assistant asks to save, Knowledge asks you directly through your MCP client (MCP elicitation) and shows the notebook, title and full text. The assistant can't answer that question for you.
- Some MCP clients can't ask you questions yet. In those, Knowledge refuses to write unless you've pre-approved the notebook with `KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS`, or turned approval off with `KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM=false`.
- Before anything is written, text that looks like a credential (API keys, tokens, private keys, passwords in URLs) is refused. The error names the kind of credential found, never the value.
- Use an `https://` address for your server, and give the password only to Knowledge, not to other tools.

## Develop

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest
```

## License

MIT. Published by Seaynic Labs.
