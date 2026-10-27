"""Local approval log: one JSON line per approval asked, granted, declined or refused.

Written to $KNOWLEDGE_APPROVAL_LOG, or $XDG_STATE_HOME/knowledge/approvals.jsonl
(~/.local/state/knowledge/approvals.jsonl). It never leaves the machine. Note and source
bodies and file contents are never logged, only their sizes; titles, notebook names and
the host of a link are logged, because those are what a person approves.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Mapping


def log_path(env: Mapping[str, str]) -> Path:
    if env.get("KNOWLEDGE_APPROVAL_LOG"):
        return Path(env["KNOWLEDGE_APPROVAL_LOG"]).expanduser()
    base = env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "knowledge" / "approvals.jsonl"


def record(env: Mapping[str, str], tool: str, decision: str, by: str | None, details: dict) -> None:
    """decision: requested | approved | declined | refused. Never raises: logging must not break a write."""
    try:
        path = log_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": tool,
                "decision": decision, "approved_by": by, **details}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass
