# Security policy

## Reporting a vulnerability

Report it privately on GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Please don't open a public issue.

We acknowledge reports within 2 business days and aim to ship a fix for confirmed high-severity issues within 14 days. We credit reporters in the release notes unless you ask us not to.

## What Knowledge does and doesn't do

- It connects only to the Open Notebook server you configure (`KNOWLEDGE_OPEN_NOTEBOOK_URL`). Open Notebook fetches the links you add; Knowledge does not. It has no telemetry and keeps no data of its own.
- Credentials come from environment variables or `~/.config/knowledge/config.toml`. They are never tool arguments and never returned to the model. Error messages name the kind of credential found, never the value.
- Saving a note or a source needs a person's approval, given in the app or ahead of time in settings. A local file is always approved by a person, with its full path and size shown. The model's own `confirm=true` is never enough.
- Local files whose names suggest credentials (`.env`, `.netrc`, private keys, `*.pem`, `*.key`, and anything inside `~/.ssh`, `~/.aws` or `~/.gnupg`) are refused before anything is read for sending.
- Text that looks like a credential is refused before anything is sent, including the text of a local file that decodes as plain text.
- Every approval, decline and refusal is logged locally in `~/.local/state/knowledge/approvals.jsonl`. The log holds titles, notebook names and sizes, never bodies or file contents.
- `tool-manifest.lock.json` records a hash of every tool description, prompt and the server instructions. CI fails if they change without the lock changing too.

## Supported versions

The latest minor release receives security fixes.
