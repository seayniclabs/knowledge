"""One error shape for every module, so scripts can rely on it.

Every failure a tool reports reads: ``knowledge.<module>.<code>: <message> Fix: <hint>``.
"""

from mcp.server.fastmcp.exceptions import ToolError


class KnowledgeError(ToolError):
    def __init__(self, module: str, code: str, message: str, hint: str = ""):
        self.module = module
        self.code = code
        text = f"knowledge.{module}.{code}: {message}"
        if hint:
            text += f" Fix: {hint}"
        super().__init__(text)


def not_configured(module: str, missing: list[str]) -> KnowledgeError:
    return KnowledgeError(
        module,
        "not_configured",
        f"missing setting(s) {', '.join(missing)}.",
        "set them as environment variables or in the [" + module + "] section of the "
        "config file, then restart the server. Call knowledge_modules to see every setting.",
    )
