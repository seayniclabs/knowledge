#!/usr/bin/env python3
"""Fail if any tracked file contains a forbidden string.

The patterns are not stored in this repository. Pass them in with
``--patterns FILE`` or the ``IDENTIFIER_PATTERNS`` environment variable
(one case-insensitive regular expression per line; ``#`` starts a comment).
A line starting with ``!`` is an allowed exception: a match that also matches
an exception is ignored.

Usage:  python scripts/scan_identifiers.py --patterns ~/patterns.txt [path ...]
Exit status 0 = clean, 1 = hits found, 2 = no patterns given.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def load(text: str) -> tuple[list[re.Pattern], list[re.Pattern]]:
    block, allow = [], []
    for line in text.splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        (allow if line.startswith("!") else block).append(re.compile(line.lstrip("!"), re.I))
    return block, allow


def tracked_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root,
                             capture_output=True, text=True, check=True).stdout
        return [root / p for p in out.splitlines()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        skip = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
        return [p for p in root.rglob("*") if p.is_file() and not skip & set(p.parts)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patterns", help="file of patterns (default: $IDENTIFIER_PATTERNS)")
    ap.add_argument("paths", nargs="*", default=["."])
    args = ap.parse_args()

    text = Path(args.patterns).expanduser().read_text() if args.patterns else os.environ.get("IDENTIFIER_PATTERNS", "")
    block, allow = load(text)
    if not block:
        print("scan_identifiers: no patterns given (use --patterns or IDENTIFIER_PATTERNS)", file=sys.stderr)
        return 2

    hits = 0
    for root in map(Path, args.paths):
        for f in tracked_files(root) if root.is_dir() else [root]:
            try:
                lines = f.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                for pat in block:
                    for m in pat.finditer(line):
                        if any(a.search(line) for a in allow):
                            continue
                        hits += 1
                        # Print where, not the line itself: the line may hold the thing we're hiding.
                        print(f"{f}:{n}: matches pattern #{block.index(pat) + 1}")
    print(f"scan_identifiers: {hits} hit(s)", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
