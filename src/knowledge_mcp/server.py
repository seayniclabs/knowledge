"""Knowledge server: one process, one tool per module operation, plus a discovery call."""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from . import __version__
from .config import Config, config_path
from .modules import ClientFactory, Module, all_modules, default_client_factory

INSTRUCTIONS = (
    "Knowledge connects to your self-hosted knowledge base (first module: Open Notebook). "
    "Call knowledge_modules first: it says which modules are on, which settings are missing, and what each tool does. "
    "Tools return structured results; errors read 'knowledge.<module>.<code>: <message> Fix: <hint>'."
)


class SettingInfo(BaseModel):
    env_var: str
    help: str
    secret: bool
    set: bool


class ModuleInfo(BaseModel):
    name: str
    summary: str
    enabled: bool
    configured: bool
    missing: list[str]
    tools: dict[str, str]
    settings: list[SettingInfo] | None = None


class ModulesResult(BaseModel):
    version: str
    config_file: str
    modules: list[ModuleInfo]


def build_server(
    config: Config | None = None,
    client_factory: ClientFactory = default_client_factory,
    modules: list[Module] | None = None,
    **settings,
) -> FastMCP:
    config = config or Config()
    modules = modules if modules is not None else all_modules()
    mcp = FastMCP("knowledge", instructions=INSTRUCTIONS, **settings)
    # FastMCP 1.x reports the SDK's version unless the low-level server is told otherwise.
    mcp._mcp_server.version = __version__

    for module in modules:
        if config.enabled(module.name, module.default_enabled):
            module.register(mcp, config, client_factory)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    def knowledge_modules(module: str | None = None) -> ModulesResult:
        """Which modules are on, what settings are missing, and what each tool does.
        Pass a module name for its full settings list."""
        infos = []
        for m in modules:
            if module and m.name != module:
                continue
            missing = m.missing(config)
            info = ModuleInfo(
                name=m.name, summary=m.summary,
                enabled=config.enabled(m.name, m.default_enabled),
                configured=not missing, missing=missing, tools=m.tools,
            )
            if module:
                info.settings = [
                    SettingInfo(env_var=s.env_var(m.name), help=s.help, secret=s.secret,
                                set=bool(config.get(m.name, s.key)))
                    for s in m.settings
                ]
            infos.append(info)
        return ModulesResult(version=__version__, config_file=str(config_path(config.env)), modules=infos)

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="knowledge-mcp", description=INSTRUCTIONS)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP only. Keep 127.0.0.1 unless you add auth in front.")
    parser.add_argument("--port", type=int, default=8765, help="HTTP only.")
    parser.add_argument("--version", action="version", version=f"knowledge-mcp {__version__}")
    args = parser.parse_args(argv)

    if args.transport == "http":
        build_server(host=args.host, port=args.port).run(transport="streamable-http")
    else:
        build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
