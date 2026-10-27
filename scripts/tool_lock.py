#!/usr/bin/env python3
"""Lock the text the model reads, so it can't change silently.

Writes tool-manifest.lock.json: a SHA-256 of every tool's name, description, input and
output schema and annotations, every prompt's description and text, and the server
instructions. `--check` (CI) fails when the live server differs from the lock. Users
can run the same check against an installed version to see that nothing moved.

Usage: python scripts/tool_lock.py [--check]
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "tool-manifest.lock.json"
sys.path.insert(0, str(ROOT / "src"))

from knowledge_mcp import __version__  # noqa: E402
from knowledge_mcp.config import Config  # noqa: E402
from knowledge_mcp.server import build_server  # noqa: E402



def _clean(d: dict) -> dict:
    """Same text on every Python: 3.13 strips docstring indentation, earlier versions keep it."""
    if d.get("description"):
        d["description"] = inspect.cleandoc(d["description"])
    return d

def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def snapshot() -> dict:
    srv = build_server(Config(env={}, file_data={}))
    tools = asyncio.run(srv.list_tools())
    prompts = asyncio.run(srv.list_prompts())
    entries = {f"tool:{t.name}": digest(_clean(t.model_dump(exclude_none=True, mode="json"))) for t in tools}
    for p in prompts:
        text = asyncio.run(srv.get_prompt(p.name)).messages[0].content.text
        entries[f"prompt:{p.name}"] = digest({"description": inspect.cleandoc(p.description or ""), "text": text})
    entries["instructions"] = digest(srv.instructions or "")
    return {"bundle": "knowledge", "version": __version__, "entries": dict(sorted(entries.items()))}


def main() -> int:
    snap = snapshot()
    if "--check" in sys.argv:
        locked = json.loads(LOCK.read_text()) if LOCK.exists() else {"entries": {}}
        changed = sorted(k for k in set(snap["entries"]) | set(locked["entries"])
                         if snap["entries"].get(k) != locked["entries"].get(k))
        for k in changed:
            print(f"changed since the lock: {k}")
        print(f"tool_lock: {len(snap['entries'])} entries, {len(changed)} changed", file=sys.stderr)
        return 1 if changed else 0
    LOCK.write_text(json.dumps(snap, indent=2) + "\n")
    print(f"wrote {LOCK.name}: {len(snap['entries'])} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
