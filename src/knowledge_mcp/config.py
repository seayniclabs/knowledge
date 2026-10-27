"""Settings come from environment variables, then an optional TOML file.

Environment variables win. The file is ``$KNOWLEDGE_CONFIG`` if set, otherwise
``$XDG_CONFIG_HOME/knowledge/config.toml`` (``~/.config/knowledge/config.toml``).
Each module reads its own ``[<module>]`` table; ``KNOWLEDGE_<MODULE>_<KEY>`` overrides
any key in it. Nothing here assumes a particular MCP client.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Mapping

TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}


def config_path(env: Mapping[str, str]) -> Path:
    if env.get("KNOWLEDGE_CONFIG"):
        return Path(env["KNOWLEDGE_CONFIG"]).expanduser()
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "knowledge" / "config.toml"


class Config:
    def __init__(self, env: Mapping[str, str] | None = None, file_data: dict | None = None):
        self.env = dict(os.environ if env is None else env)
        if file_data is None:
            path = config_path(self.env)
            file_data = tomllib.loads(path.read_text()) if path.is_file() else {}
        self.file = file_data

    def get(self, module: str, key: str, default: str | None = None) -> str | None:
        value = self.env.get(f"KNOWLEDGE_{module.upper()}_{key.upper()}")
        if value not in (None, ""):
            return value
        value = self.file.get(module, {}).get(key)
        if value not in (None, ""):
            return str(value)
        return default

    def flag(self, module: str, key: str, default: bool) -> bool:
        value = self.get(module, key)
        if value is None:
            return default
        value = value.strip().lower()
        if value in TRUE:
            return True
        if value in FALSE:
            return False
        return default

    def enabled(self, module: str, default: bool) -> bool:
        return self.flag(module, "enabled", default)
