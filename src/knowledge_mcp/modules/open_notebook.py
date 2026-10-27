"""Open Notebook: notebooks, search, sources and notes, over its REST API.

Endpoints and shapes were read from Open Notebook's own FastAPI routers (api/routers/*.py):
GET /api/notebooks, POST /api/search, GET /api/notes, GET/POST /api/notes,
GET /api/notes/{id}, GET /api/sources/{id}, GET /api/notebooks (for names).
Auth: when the server has OPEN_NOTEBOOK_PASSWORD set, every /api call needs
``Authorization: Bearer <password>``; with no password set, no header is needed.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ..config import Config
from ..errors import KnowledgeError
from ..scrub import check_text
from . import ClientFactory, Module, Setting

MODULE = "open_notebook"
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITES_NOTE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

MAX_NOTE_CHARS = 180_000
MAX_TITLE_CHARS = 200
MAX_QUERY_CHARS = 500
MAX_SNIPPET_CHARS = 500
MAX_FETCH = 200  # most items any one call will ask the server for

NOTEBOOK_ID = re.compile(r"notebook:[A-Za-z0-9_-]{1,120}")
NOTE_ID = re.compile(r"note:[A-Za-z0-9_-]{1,120}")
SOURCE_ID = re.compile(r"source:[A-Za-z0-9_-]{1,120}")


# --- typed results ----------------------------------------------------------

class NotebookInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    archived: bool = False
    source_count: int = 0
    note_count: int = 0
    updated: str = ""


class NotebookPage(BaseModel):
    notebooks: list[NotebookInfo]
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None = None


class SearchHit(BaseModel):
    id: str
    kind: Literal["source", "note", "insight", "other"]
    title: str | None = None
    snippet: str | None = Field(None, description="Matched text; matched words are wrapped in backticks.")
    parent_id: str | None = None
    score: float | None = Field(None, description="Relevance (text) or similarity (vector). Higher is better.")


class SearchPage(BaseModel):
    query: str
    mode: Literal["text", "vector"]
    notebook_id: str | None = None
    hits: list[SearchHit]
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None = None


class NoteSummary(BaseModel):
    id: str
    title: str | None = None
    note_type: str | None = None
    updated: str = ""


class NotePage(BaseModel):
    notebook_id: str
    notes: list[NoteSummary]
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None = None


class NoteDetail(BaseModel):
    id: str
    title: str | None = None
    note_type: str | None = None
    content: str | None = None
    content_chars: int
    truncated: bool
    created: str = ""
    updated: str = ""


class SourceDetail(BaseModel):
    id: str
    title: str | None = None
    topics: list[str] = []
    embedded: bool = False
    embedded_chunks: int = 0
    notebooks: list[str] = []
    status: str | None = None
    full_text: str | None = None
    content_chars: int = 0
    truncated: bool = False
    created: str = ""
    updated: str = ""


class NoteWrite(BaseModel):
    created: bool = Field(description="True only when the note was saved.")
    title: str
    notebook_id: str
    notebook_name: str
    content_chars: int
    id: str | None = None
    preview: str | None = Field(None, description="Start of the content, when nothing was saved yet.")
    approved_by: str | None = Field(None, description='"person", "allowlist" or "settings".')
    embedding_command_id: str | None = None
    note: str | None = None


class WriteApproval(BaseModel):
    create: bool = Field(description="Save this note to the notebook now?")


# --- helpers ----------------------------------------------------------------

def _window(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, 100)), max(0, offset)


def _page_bounds(offset: int, limit: int, total_seen: int) -> tuple[bool, int | None]:
    has_more = total_seen > offset + limit
    return has_more, (offset + limit if has_more else None)


def _check_id(value: str, pattern: re.Pattern[str], field: str, shape: str) -> str:
    if not pattern.fullmatch(value or ""):
        raise KnowledgeError(
            MODULE, "bad_input", f"{field} {value!r} is not a valid id.",
            f"pass an id of the form {shape}, as returned by the list or search tools.",
        )
    return value


def _clip(text: Any, max_chars: int) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    s = str(text)
    if len(s) <= max_chars:
        return s, False
    return s[:max_chars], True


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _kind(item_id: str) -> str:
    prefix = item_id.split(":", 1)[0]
    return {"source": "source", "note": "note", "source_insight": "insight"}.get(prefix, "other")


def _allowlist(config: Config) -> list[str]:
    raw = config.get(MODULE, "allowed_notebooks") or ""
    return [n.strip().lower() for n in raw.split(",") if n.strip()]


def _can_ask_person(ctx: Context) -> bool:
    try:
        caps = ctx.session.client_params.capabilities  # type: ignore[union-attr]
    except (ValueError, AttributeError):
        return False
    return bool(caps and caps.elicitation)


def _approval_text(title: str, notebook_name: str, content: str) -> str:
    return "\n".join([
        "Save this note to Open Notebook?",
        f"Notebook: {notebook_name!r}",
        f"Title: {title!r}",
        f"Content ({len(content)} characters):",
        content,
    ])


# --- module -----------------------------------------------------------------

class OpenNotebookModule(Module):
    def __init__(self) -> None:
        super().__init__(
            name=MODULE,
            summary="Your self-hosted Open Notebook: list notebooks, search, read notes and sources, "
                    "and create a note (you approve each one).",
            settings=[
                Setting("url", "Address of your Open Notebook server, e.g. https://notebook.example.com "
                        "(no path). Required for every tool."),
                Setting("password", "The password you set on the server with OPEN_NOTEBOOK_PASSWORD. "
                        "Leave unset if the server has no password.", secret=True, required=False),
                Setting("require_confirm", "true (default): you approve each new note. false: the "
                        "assistant may create notes on its own.", required=False),
                Setting("allowed_notebooks", "Comma-separated notebook names the assistant may write to "
                        "without asking, e.g. Research,Inbox.", required=False),
            ],
            tools={
                "open_notebook_notebooks": "List notebooks (paged).",
                "open_notebook_search": "Search sources and notes by keyword (text) or meaning (vector).",
                "open_notebook_notes": "List the notes in one notebook (paged, titles only).",
                "open_notebook_note_get": "Read one note's content.",
                "open_notebook_source_get": "Read one source: its title, topics and extracted text.",
                "open_notebook_note_create": "Create a note in a notebook. Preview first; you approve it.",
            },
        )

    def register(self, mcp: FastMCP, config: Config, client_factory: ClientFactory) -> None:
        module = self

        def base_url() -> str:
            url = module.require(config, "url")["url"].strip().rstrip("/")
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise KnowledgeError(
                    MODULE, "bad_url", "KNOWLEDGE_OPEN_NOTEBOOK_URL must start with http:// or https:// "
                    "and name a host.",
                    "use the address you open in a browser, e.g. https://notebook.example.com.",
                )
            return url

        async def call(method: str, path: str, *, params: dict[str, Any] | None = None,
                       json_body: dict[str, Any] | None = None) -> Any:
            url = base_url()
            headers: dict[str, str] = {}
            password = config.get(MODULE, "password")
            if password:
                headers["Authorization"] = f"Bearer {password}"
            try:
                async with client_factory() as client:
                    r = await client.request(method, f"{url}/api{path}", params=params,
                                             json=json_body, headers=headers)
            except httpx.HTTPError as e:
                raise KnowledgeError(MODULE, "unreachable",
                                     f"could not reach Open Notebook ({type(e).__name__}).",
                                     "check KNOWLEDGE_OPEN_NOTEBOOK_URL and that the server is running.")
            if r.status_code == 401:
                raise KnowledgeError(MODULE, "auth_failed", "Open Notebook rejected the request (401).",
                                     "set KNOWLEDGE_OPEN_NOTEBOOK_PASSWORD to the server's password, or "
                                     "unset it if the server has no password.")
            if r.status_code >= 400:
                detail = ""
                try:
                    body = r.json()
                    detail = str(body.get("detail", "")) if isinstance(body, dict) else ""
                except ValueError:
                    pass
                detail = (detail or r.reason_phrase or "error")[:200]
                if r.status_code == 404:
                    raise KnowledgeError(MODULE, "not_found", f"Open Notebook: {detail}.",
                                         "check the id with the list or search tools.")
                raise KnowledgeError(MODULE, "api_error", f"Open Notebook returned {r.status_code}: {detail}.")
            try:
                return r.json()
            except ValueError:
                raise KnowledgeError(MODULE, "api_error", "Open Notebook did not return JSON.",
                                     "check that KNOWLEDGE_OPEN_NOTEBOOK_URL points at the server, not a web page.")

        async def notebook_name(notebook_id: str) -> str:
            data = await call("GET", "/notebooks")
            for nb in data if isinstance(data, list) else []:
                if str(nb.get("id", "")) == notebook_id:
                    return str(nb.get("name") or "")
            raise KnowledgeError(MODULE, "not_found", f"no notebook with id {notebook_id!r}.",
                                 "call open_notebook_notebooks to list the ids.")

        @mcp.tool(annotations=READ_ONLY)
        async def open_notebook_notebooks(limit: int = 20, offset: int = 0,
                                          include_archived: bool = False) -> NotebookPage:
            """List your notebooks, most recently updated first. Page with offset=<next_offset>."""
            limit, offset = _window(limit, offset)
            params: dict[str, Any] = {"order_by": "updated desc"}
            if not include_archived:
                params["archived"] = "false"
            data = await call("GET", "/notebooks", params=params)
            items = data if isinstance(data, list) else []
            window = items[offset: offset + limit]
            has_more, nxt = _page_bounds(offset, limit, len(items))
            return NotebookPage(
                notebooks=[NotebookInfo(
                    id=str(nb.get("id", "")), name=str(nb.get("name") or ""),
                    description=str(nb.get("description") or ""), archived=bool(nb.get("archived")),
                    source_count=int(nb.get("source_count") or 0), note_count=int(nb.get("note_count") or 0),
                    updated=str(nb.get("updated") or ""),
                ) for nb in window],
                offset=offset, limit=limit, has_more=has_more, next_offset=nxt,
            )

        @mcp.tool(annotations=READ_ONLY)
        async def open_notebook_search(
            query: str,
            notebook_id: str | None = None,
            mode: Literal["text", "vector"] = "text",
            include_sources: bool = True,
            include_notes: bool = True,
            limit: int = 10,
            offset: int = 0,
        ) -> SearchPage:
            """Search sources and notes. mode 'text' matches keywords; 'vector' matches meaning and
            needs an embedding model set up on the server. Limit a search to one notebook with
            notebook_id. Page with offset=<next_offset>."""
            q = query.strip()
            if not q:
                raise KnowledgeError(MODULE, "bad_input", "query is empty.", "pass the words to search for.")
            if len(q) > MAX_QUERY_CHARS:
                raise KnowledgeError(MODULE, "bad_input", f"query is longer than {MAX_QUERY_CHARS} characters.",
                                     "shorten it.")
            if notebook_id is not None:
                _check_id(notebook_id, NOTEBOOK_ID, "notebook_id", "notebook:<id>")
            limit, offset = _window(limit, offset)
            need = offset + limit + 1  # one extra row tells us whether another page exists
            if need > MAX_FETCH:
                raise KnowledgeError(MODULE, "bad_input", "offset is too large for search paging.",
                                     "narrow the query with notebook_id, or use a smaller offset.")
            body: dict[str, Any] = {"query": q, "type": mode, "limit": need,
                                    "search_sources": include_sources, "search_notes": include_notes}
            if notebook_id:
                body["notebook_ids"] = [notebook_id]
            data = await call("POST", "/search", json_body=body)
            raw = data.get("results", []) if isinstance(data, dict) else []
            hits = []
            for r in raw:
                item_id = str(r.get("id", ""))
                snippet, _ = _clip(r.get("content"), MAX_SNIPPET_CHARS)
                parent = r.get("parent_id")
                hits.append(SearchHit(
                    id=item_id, kind=_kind(item_id),  # type: ignore[arg-type]
                    title=r.get("title"), snippet=snippet,
                    parent_id=str(parent) if parent else None,
                    score=_float(r.get("relevance", r.get("similarity"))),
                ))
            window = hits[offset: offset + limit]
            has_more, nxt = _page_bounds(offset, limit, len(hits))
            return SearchPage(query=q, mode=mode, notebook_id=notebook_id, hits=window,
                              offset=offset, limit=limit, has_more=has_more, next_offset=nxt)

        @mcp.tool(annotations=READ_ONLY)
        async def open_notebook_notes(notebook_id: str, limit: int = 20, offset: int = 0) -> NotePage:
            """List the notes in one notebook, titles only. Read a note with open_notebook_note_get.
            Page with offset=<next_offset>."""
            _check_id(notebook_id, NOTEBOOK_ID, "notebook_id", "notebook:<id>")
            limit, offset = _window(limit, offset)
            data = await call("GET", "/notes", params={"notebook_id": notebook_id})
            items = data if isinstance(data, list) else []
            window = items[offset: offset + limit]
            has_more, nxt = _page_bounds(offset, limit, len(items))
            return NotePage(
                notebook_id=notebook_id,
                notes=[NoteSummary(id=str(n.get("id", "")), title=n.get("title"),
                                   note_type=n.get("note_type"), updated=str(n.get("updated") or ""))
                       for n in window],
                offset=offset, limit=limit, has_more=has_more, next_offset=nxt,
            )

        @mcp.tool(annotations=READ_ONLY)
        async def open_notebook_note_get(note_id: str, max_chars: int = 8000) -> NoteDetail:
            """Read one note. Long content is cut at max_chars (up to 50000) and marked truncated."""
            _check_id(note_id, NOTE_ID, "note_id", "note:<id>")
            limit = max(200, min(max_chars, 50_000))
            n = await call("GET", f"/notes/{note_id}")
            content, truncated = _clip(n.get("content"), limit)
            return NoteDetail(id=str(n.get("id", note_id)), title=n.get("title"), note_type=n.get("note_type"),
                              content=content, content_chars=len(str(n.get("content") or "")),
                              truncated=truncated, created=str(n.get("created") or ""),
                              updated=str(n.get("updated") or ""))

        @mcp.tool(annotations=READ_ONLY)
        async def open_notebook_source_get(source_id: str, max_chars: int = 8000) -> SourceDetail:
            """Read one source: its title, topics, extracted text (cut at max_chars, up to 50000) and
            the notebooks it belongs to."""
            _check_id(source_id, SOURCE_ID, "source_id", "source:<id>")
            limit = max(200, min(max_chars, 50_000))
            s = await call("GET", f"/sources/{source_id}")
            text, truncated = _clip(s.get("full_text"), limit)
            return SourceDetail(
                id=str(s.get("id", source_id)), title=s.get("title"),
                topics=[str(t) for t in (s.get("topics") or [])],
                embedded=bool(s.get("embedded")), embedded_chunks=int(s.get("embedded_chunks") or 0),
                notebooks=[str(n) for n in (s.get("notebooks") or [])], status=s.get("status"),
                full_text=text, content_chars=len(str(s.get("full_text") or "")), truncated=truncated,
                created=str(s.get("created") or ""), updated=str(s.get("updated") or ""),
            )

        @mcp.tool(annotations=WRITES_NOTE)
        async def open_notebook_note_create(
            ctx: Context, notebook_id: str, title: str, content: str, confirm: bool = False,
        ) -> NoteWrite:
            """Create a note in a notebook. Without confirm=true it only returns a preview. With
            confirm=true you are asked to approve it (unless the notebook is pre-approved in settings);
            the assistant cannot approve on your behalf. Text that looks like a credential is refused."""
            _check_id(notebook_id, NOTEBOOK_ID, "notebook_id", "notebook:<id>")
            title = title.strip()
            if not title or len(title) > MAX_TITLE_CHARS or any(ord(c) < 32 for c in title):
                raise KnowledgeError(MODULE, "bad_input",
                                     f"title must be 1 to {MAX_TITLE_CHARS} characters with no control characters.",
                                     "give the note a short plain title.")
            if not content.strip():
                raise KnowledgeError(MODULE, "bad_input", "content is empty.", "provide the note text.")
            if len(content) > MAX_NOTE_CHARS:
                raise KnowledgeError(MODULE, "bad_input", f"content is longer than {MAX_NOTE_CHARS} characters.",
                                     "split it into several notes.")
            check_text(title, "title")
            check_text(content, "content")

            name = await notebook_name(notebook_id)
            allowlist = _allowlist(config)
            outside = bool(allowlist) and name.lower() not in allowlist
            base = dict(title=title, notebook_id=notebook_id, notebook_name=name, content_chars=len(content))

            if not confirm:
                note = "Preview only. Call again with confirm=true; you will be asked to approve it."
                if outside:
                    note = (f"Preview only. Notebook {name!r} is not in "
                            "KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS, so you will be asked to approve it.")
                return NoteWrite(created=False, preview=content[:600], note=note, **base)

            # Approval comes from the person or from settings, never from the model's own arguments.
            approved_by = None
            if not config.flag(MODULE, "require_confirm", True):
                approved_by = "settings"
            elif allowlist and not outside:
                approved_by = "allowlist"
            elif _can_ask_person(ctx):
                answer = await ctx.elicit(_approval_text(title, name, content), WriteApproval)
                if answer.action != "accept" or not answer.data.create:
                    return NoteWrite(created=False, note="You declined. Nothing was saved.", **base)
                approved_by = "person"
            else:
                raise KnowledgeError(
                    MODULE, "approval_unavailable",
                    "this MCP client cannot ask you to approve a write, and the notebook is not pre-approved.",
                    "set KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS to the notebook names the assistant may write "
                    "to, or KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM=false to let it write on its own.",
                )

            data = await call("POST", "/notes", json_body={
                "title": title, "content": content, "note_type": "human", "notebook_id": notebook_id,
            })
            return NoteWrite(
                created=True, id=data.get("id"), approved_by=approved_by,
                embedding_command_id=data.get("command_id"),
                note="Saved. Search indexing runs in the background and may take a moment.", **base,
            )
