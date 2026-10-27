"""Serve the bundle's skills as MCP prompts and resources.

The skills live in the repo's ``skills/<name>/SKILL.md`` (Agent Skills format), which
Claude Code and other skill-aware apps read directly. Apps that only speak MCP get the
same text here: one prompt per skill, and every file in a skill folder as a resource at
``skill://<bundle>/<skill>/<path>``. One source, two delivery routes.
"""

from __future__ import annotations

import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import Prompt
from mcp.server.fastmcp.resources import TextResource

TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".tsx", ".html", ".css", ".toml", ".csv", ".xml", ".svg"}


def skills_dir() -> Path | None:
    """The installed copy (packaged into the wheel), or the repo folder in a checkout."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "skills", here.parent.parent / "skills"):
        if candidate.is_dir():
            return candidate
    return None


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Top-level scalar keys of a SKILL.md header (folded ``>``/``|`` values joined), and the body."""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line and not line[0].isspace() and ":" in line:
            key, _, value = line.partition(":")
            value = value.strip()
            if value in (">", "|", ">-", "|-", ">+", "|+"):
                parts = []
                while i + 1 < len(lines) and (not lines[i + 1].strip() or lines[i + 1][0].isspace()):
                    i += 1
                    parts.append(lines[i].strip())
                value = " ".join(p for p in parts if p)
            meta[key.strip()] = value.strip("\"'")
        i += 1
    return meta, m.group(2)


def load(root: Path | None = None) -> list[dict]:
    root = root or skills_dir()
    if not root:
        return []
    out = []
    for folder in sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file()):
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        meta, body = frontmatter(text)
        files = sorted(
            f for f in folder.rglob("*")
            if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES and f.name != "SKILL.md"
        )
        out.append({"name": meta.get("name", folder.name), "description": meta.get("description", ""),
                    "license": meta.get("license", ""), "folder": folder, "body": body, "files": files})
    return out


def register(mcp: FastMCP, bundle: str, root: Path | None = None) -> list[dict]:
    skills = load(root)
    for s in skills:
        base = f"skill://{bundle}/{s['name']}"
        extra = [f"{base}/{f.relative_to(s['folder']).as_posix()}" for f in s["files"]]
        prompt_text = s["body"]
        if extra:
            prompt_text += "\n\n---\nSupporting files for this skill (MCP resources):\n" + "\n".join(f"- {u}" for u in extra)

        def make(text: str):
            def skill_prompt() -> str:
                return text
            return skill_prompt

        mcp.add_prompt(Prompt.from_function(make(prompt_text), name=s["name"], description=s["description"][:1024]))
        mcp.add_resource(TextResource(uri=f"{base}/SKILL.md", name=f"{s['name']}/SKILL.md",
                                      text=(s["folder"] / "SKILL.md").read_text(encoding="utf-8"), mime_type="text/markdown"))
        for f, uri in zip(s["files"], extra):
            mcp.add_resource(TextResource(uri=uri, name=f"{s['name']}/{f.relative_to(s['folder']).as_posix()}",
                                          text=f.read_text(encoding="utf-8", errors="replace"), mime_type="text/plain"))
    return skills
