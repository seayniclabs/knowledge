"""Module contract and registry.

A module is one integration (Open Notebook, ...). It declares its settings and
registers its tools on the shared server. Adding a module = adding a file here and
listing it in ``all_modules``. Modules added after 0.1 must set ``default_enabled = False``
so an update never turns on something the user did not choose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import httpx
from mcp.server.fastmcp import FastMCP

from ..config import Config
from ..errors import not_configured

ClientFactory = Callable[[], httpx.AsyncClient]


def default_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60.0)


@dataclass(frozen=True)
class Setting:
    key: str
    help: str
    secret: bool = False
    required: bool = True

    def env_var(self, module: str) -> str:
        return f"KNOWLEDGE_{module.upper()}_{self.key.upper()}"


@dataclass
class Module:
    name: str
    summary: str
    settings: list[Setting]
    default_enabled: bool = True
    tools: dict[str, str] = field(default_factory=dict)  # tool name -> one-line purpose

    def missing(self, config: Config) -> list[str]:
        return [
            s.env_var(self.name)
            for s in self.settings
            if s.required and not config.get(self.name, s.key)
        ]

    def require(self, config: Config, *keys: str) -> dict[str, str]:
        """Return the named settings, or raise a not_configured error naming the env vars."""
        values = {k: config.get(self.name, k) for k in keys}
        absent = [
            s.env_var(self.name) for s in self.settings if s.key in keys and not values[s.key]
        ]
        if absent:
            raise not_configured(self.name, absent)
        return values  # type: ignore[return-value]

    def register(self, mcp: FastMCP, config: Config, client_factory: ClientFactory) -> None:
        raise NotImplementedError


def all_modules() -> list[Module]:
    from .open_notebook import OpenNotebookModule

    return [OpenNotebookModule()]
