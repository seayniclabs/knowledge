"""`knowledge-bundle-mcp --doctor`: check settings and reachability, print a fix for each problem.

Makes only the calls a person asked for by running it: one read-only GET of your
notebooks list on the Open Notebook server you configure. Exit status 0 = ready,
1 = something to fix.
"""

from __future__ import annotations

import asyncio
import sys
from urllib.parse import urlsplit

import httpx

from . import __version__
from .approvals import log_path
from .config import Config, config_path
from .modules import all_modules


async def _checks(config: Config, transport: httpx.AsyncBaseTransport | None = None) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    path = config_path(config.env)
    out.append(("config file", True, f"{path} ({'found' if path.is_file() else 'not used'})"))
    async with httpx.AsyncClient(timeout=8.0, transport=transport) as client:
        for m in all_modules():
            if not config.enabled(m.name, m.default_enabled):
                out.append((m.name, True, "switched off"))
                continue
            missing = m.missing(config)
            if missing:
                out.append((m.name, False, f"missing {', '.join(missing)}. Add them to your MCP app's env or to {path}."))
                continue
            if m.name == "open_notebook":
                out.append(await _open_notebook(client, config))
    out.append(("approval log", True, f"{log_path(config.env)} (written when you approve, decline or are refused a write)"))
    out.append(("approval ask", True, "a write asks you through your MCP client. This command cannot tell "
                                       "whether your client can ask, so test with a preview first."))
    return out


async def _open_notebook(client: httpx.AsyncClient, config: Config) -> tuple[str, bool, str]:
    url = (config.get("open_notebook", "url") or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ("open_notebook", False, "KNOWLEDGE_OPEN_NOTEBOOK_URL must start with http:// or https:// and name a host. "
                                        "Use the address you open in a browser.")
    headers = {}
    password = config.get("open_notebook", "password")
    if password:
        headers["Authorization"] = f"Bearer {password}"
    try:
        r = await client.get(f"{url}/api/notebooks", headers=headers)
    except httpx.HTTPError as e:
        return ("open_notebook", False, f"could not connect ({type(e).__name__}). Check KNOWLEDGE_OPEN_NOTEBOOK_URL "
                                        "and that the server is running.")
    if r.status_code == 401:
        return ("open_notebook", False, "the server rejected the request (401). Set KNOWLEDGE_OPEN_NOTEBOOK_PASSWORD "
                                        "to the server's password, or unset it if the server has no password.")
    if r.status_code >= 400:
        return ("open_notebook", False, f"the server answered {r.status_code}. Check that the address is the Open "
                                        "Notebook server itself.")
    try:
        count = len(r.json())
    except ValueError:
        return ("open_notebook", False, "the address answered, but not with JSON. Check that it points at the "
                                        "Open Notebook server, not a web page.")
    auth = "password accepted" if password else "no password needed"
    return ("open_notebook", True, f"{url} answered 200 ({count} notebooks, {auth})")


def run(config: Config | None = None, transport: httpx.AsyncBaseTransport | None = None) -> int:
    config = config or Config()
    results = asyncio.run(_checks(config, transport))
    print(f"knowledge-bundle-mcp {__version__} doctor")
    for name, ok, msg in results:
        print(f"  {'ok ' if ok else 'FIX'}  {name:<13} {msg}")
    bad = sum(1 for _, ok, _ in results if not ok)
    print("ready" if not bad else f"{bad} thing(s) to fix")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run())
