"""Open Notebook: notebooks, search, sources and notes, over its REST API.

Endpoints and shapes were read from Open Notebook's own FastAPI routers (api/routers/*.py):
GET /api/notebooks, POST /api/search, GET /api/notes, GET/POST /api/notes,
GET /api/notes/{id}, GET /api/sources/{id}, GET /api/notebooks (for names).
Auth: when the server has OPEN_NOTEBOOK_PASSWORD set, every /api call needs
``Authorization: Bearer <password>``; with no password set, no header is needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ..approvals import record as log_decision
from ..config import Config
from ..errors import KnowledgeError
from ..scrub import check_text, find_credential_kinds
from . import ClientFactory, Module, Setting

MODULE = "open_notebook"
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITES_NOTE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

MAX_NOTE_CHARS = 180_000
MAX_TITLE_CHARS = 200
MAX_QUERY_CHARS = 500
MAX_SNIPPET_CHARS = 500
MAX_FETCH = 200  # most items any one call will ask the server for
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # largest local file add_source will send
WRITES_SOURCE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
# Files that hold credentials by name. add_source refuses them before anything is sent.
CREDENTIAL_FILE = re.compile(
    r"(?i)^(\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|id_(rsa|dsa|ecdsa|ed25519)|credentials(\.json)?"
    r"|.*\.(pem|key|p12|pfx|kdbx|keychain-db))$"
)
CREDENTIAL_DIRS = (".ssh", ".aws", ".gnupg")

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


class SourceWrite(BaseModel):
    created: bool = Field(description="True only when the source was sent to Open Notebook.")
    kind: Literal["link", "file"]
    notebook_id: str
    notebook_name: str
    target: str = Field(description="The link without its query string, or the file name.")
    size_bytes: int | None = Field(None, description="Size of the local file, when kind is file.")
    path: str | None = Field(None, description="Full path of the local file. Set only in a preview.")
    source_id: str | None = None
    command_id: str | None = Field(None, description="Background job that processes the source.")
    status: str | None = Field(None, description="Processing status when the source was created, usually 'new'.")
    approved_by: str | None = Field(None, description='"person", "allowlist" or "settings".')
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


def _scan(config: Config, tool: str, *fields: tuple[str, str], details: dict) -> None:
    """Refuse text that looks like a credential. Logs the refusal with the kinds found, never the value."""
    for text, field in fields:
        kinds = find_credential_kinds(text)
        if kinds:
            log_decision(config.env, tool, "refused", None,
                         {**details, "reason": "blocked_secret", "field": field, "kinds": kinds})
            check_text(text, field)  # raises the readable error


def _link_parts(raw: str) -> tuple[str, str]:
    """Validate a web link. Returns (the link as sent, the link without query or fragment)."""
    link = raw.strip()
    # Control characters (newlines included) could fake extra lines in the approval prompt. Refuse them.
    if any(ord(c) < 32 or ord(c) == 127 for c in link) or " " in link:
        raise KnowledgeError(MODULE, "bad_input", "url contains spaces or control characters.",
                             "pass one plain web address. Nothing was sent.")
    parts = urlsplit(link)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise KnowledgeError(MODULE, "bad_input", "url must start with http:// or https:// and name a host.",
                             "pass the full web address of the page.")
    if parts.username is not None or parts.password is not None:
        raise KnowledgeError(MODULE, "bad_input", "url contains a user name or password.",
                             "remove the user name and password from the link. Nothing was sent.")
    display = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return link, display


def _local_file(raw: str, config: Config, details: dict) -> tuple[Path, bytes, str]:
    """Read a local file the person named, after the safety checks. Returns (path, bytes, scan result).

    Nothing is sent here. The bytes are used only if the person then approves the exact path and size.
    """
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise KnowledgeError(MODULE, "bad_input", "file_path contains control characters.",
                             "give a plain path to a file on this computer. Nothing was sent.")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise KnowledgeError(MODULE, "bad_input", "no file at that path.",
                             "give the full path to a file on this computer.")
    refuse = None
    if CREDENTIAL_FILE.match(path.name):
        refuse = "its name suggests it holds credentials"
    elif any((Path.home() / d) in path.parents for d in CREDENTIAL_DIRS):
        refuse = "it is inside a folder that holds credentials"
    if refuse:
        log_decision(config.env, "open_notebook_add_source", "refused", None,
                     {**details, "reason": "path_refused"})
        raise KnowledgeError(MODULE, "path_refused", f"Knowledge will not send this file: {refuse}. Nothing was sent.",
                             "copy the text you need into a new file without the secrets, or add a link instead.")
    size = path.stat().st_size
    if size == 0:
        raise KnowledgeError(MODULE, "bad_input", "the file is empty.", "choose a file with content.")
    if size > MAX_UPLOAD_BYTES:
        raise KnowledgeError(MODULE, "bad_input", f"the file is {size} bytes; the limit is {MAX_UPLOAD_BYTES}.",
                             "split the file or add a link instead.")
    try:
        data = path.read_bytes()
    except OSError:
        raise KnowledgeError(MODULE, "bad_input", "could not read the file.", "check that this process can read it.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return path, data, "not run (the file is not plain text)"
    _scan(config, "open_notebook_add_source", (text, "file"), details=details)
    return path, data, "passed"


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
                "open_notebook_add_source": "Add a web link or a local file as a source in a notebook. "
                                            "Preview first; you approve it. Processed in the background.",
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
                       json_body: dict[str, Any] | None = None, form: dict[str, str] | None = None,
                       files: dict[str, tuple[str, bytes, str]] | None = None) -> Any:
            url = base_url()
            headers: dict[str, str] = {}
            password = config.get(MODULE, "password")
            if password:
                headers["Authorization"] = f"Bearer {password}"
            try:
                async with client_factory() as client:
                    r = await client.request(method, f"{url}/api{path}", params=params,
                                             json=json_body, data=form, files=files, headers=headers)
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
            name = await notebook_name(notebook_id)
            allowlist = _allowlist(config)
            outside = bool(allowlist) and name.lower() not in allowlist
            base = dict(title=title, notebook_id=notebook_id, notebook_name=name, content_chars=len(content))
            details = {"notebook": name, "title": title, "content_chars": len(content)}
            _scan(config, "open_notebook_note_create", (title, "title"), (content, "content"), details=details)

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
                log_decision(config.env, "open_notebook_note_create", "requested", None, details)
                answer = await ctx.elicit(_approval_text(title, name, content), WriteApproval)
                if answer.action != "accept" or not answer.data.create:
                    log_decision(config.env, "open_notebook_note_create", "declined", None, details)
                    return NoteWrite(created=False, note="You declined. Nothing was saved.", **base)
                approved_by = "person"
            else:
                log_decision(config.env, "open_notebook_note_create", "refused", None,
                             {**details, "reason": "approval_unavailable"})
                raise KnowledgeError(
                    MODULE, "approval_unavailable",
                    "this MCP client cannot ask you to approve a write, and the notebook is not pre-approved.",
                    "set KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS to the notebook names the assistant may write "
                    "to, or KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM=false to let it write on its own.",
                )
            log_decision(config.env, "open_notebook_note_create", "approved", approved_by, details)

            data = await call("POST", "/notes", json_body={
                "title": title, "content": content, "note_type": "human", "notebook_id": notebook_id,
            })
            return NoteWrite(
                created=True, id=data.get("id"), approved_by=approved_by,
                embedding_command_id=data.get("command_id"),
                note="Saved. Search indexing runs in the background and may take a moment.", **base,
            )

        @mcp.tool(annotations=WRITES_SOURCE)
        async def open_notebook_add_source(
            ctx: Context, notebook_id: str, url: str | None = None, file_path: str | None = None,
            title: str | None = None, confirm: bool = False,
        ) -> SourceWrite:
            """Add a web link or a local file as a source in one notebook. Give exactly one of url or file_path.
            Without confirm=true it only returns a preview. With confirm=true you are asked to approve it; the
            assistant cannot approve for you. A local file is always approved by you, with its full path and
            size shown, and is never pre-approved in settings. Open Notebook processes the source in the
            background: check progress with open_notebook_source_get."""
            _check_id(notebook_id, NOTEBOOK_ID, "notebook_id", "notebook:<id>")
            if bool(url) == bool(file_path):
                raise KnowledgeError(MODULE, "bad_input", "give exactly one of url or file_path.",
                                     "pass a web address as url, or a file on this computer as file_path.")
            title = (title or "").strip() or None
            if title and (len(title) > MAX_TITLE_CHARS or any(ord(c) < 32 for c in title)):
                raise KnowledgeError(MODULE, "bad_input",
                                     f"title must be 1 to {MAX_TITLE_CHARS} characters with no control characters.",
                                     "give the source a short plain title, or leave it out.")
            name = await notebook_name(notebook_id)
            allowlist = _allowlist(config)
            outside = bool(allowlist) and name.lower() not in allowlist

            if url is not None:
                kind = "link"
                link, display = _link_parts(url)
                details = {"notebook": name, "kind": kind, "target": display, "title": title}
                _scan(config, "open_notebook_add_source", (link, "url"), *([(title, "title")] if title else []),
                      details=details)
                path, data, scan, size, label = None, None, "not needed (a link)", None, display
                question = ["Add this link to Open Notebook?", f"Notebook: {name!r}", f"Link: {link!r}"]
            else:
                kind = "file"
                details = {"notebook": name, "kind": kind, "target": Path(file_path).name, "title": title}
                path, data, scan = _local_file(file_path, config, details)
                size, label = len(data), path.name
                details.update(size_bytes=size)
                if title:
                    _scan(config, "open_notebook_add_source", (title, "title"), details=details)
                question = ["Upload this file to Open Notebook?", f"Notebook: {name!r}", f"File: {str(path)!r}",
                            f"Size: {size} bytes", f"Credential scan: {scan}"]
            if title:
                question.append(f"Title: {title!r}")

            if not confirm:
                note = "Preview only. Nothing was sent. Call again with confirm=true; you will be asked to approve it."
                if kind == "file":
                    note = ("Preview only. Nothing was sent. Call again with confirm=true; you will be asked to "
                            "approve this file, with its path and size shown.")
                elif outside:
                    note += f" Notebook {name!r} is not in KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS."
                return SourceWrite(created=False, kind=kind, notebook_id=notebook_id, notebook_name=name,
                                   target=label, size_bytes=size, path=str(path) if path else None, note=note)

            base = dict(kind=kind, notebook_id=notebook_id, notebook_name=name, target=label, size_bytes=size)
            approved_by = None
            if kind == "link" and not config.flag(MODULE, "require_confirm", True):
                approved_by = "settings"
            elif kind == "link" and allowlist and not outside:
                approved_by = "allowlist"
            elif _can_ask_person(ctx):
                log_decision(config.env, "open_notebook_add_source", "requested", None, details)
                answer = await ctx.elicit("\n".join(question), WriteApproval)
                if answer.action != "accept" or not answer.data.create:
                    log_decision(config.env, "open_notebook_add_source", "declined", None, details)
                    return SourceWrite(created=False, note="You declined. Nothing was sent.", **base)
                approved_by = "person"
            else:
                log_decision(config.env, "open_notebook_add_source", "refused", None,
                             {**details, "reason": "approval_unavailable"})
                hint = ("this MCP client cannot ask you to approve. Local files need an app that can ask, "
                        "because they are never pre-approved. Add a link instead, or use a client that asks.") \
                    if kind == "file" else (
                        "set KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS to the notebook names the assistant may add "
                        "sources to, or KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM=false to let it add links on its own.")
                raise KnowledgeError(MODULE, "approval_unavailable",
                                     "this MCP client cannot ask you to approve a source, and it is not pre-approved.",
                                     hint)
            log_decision(config.env, "open_notebook_add_source", "approved", approved_by, details)

            form = {"type": "link" if kind == "link" else "upload", "notebooks": json.dumps([notebook_id]),
                    "embed": "true", "async_processing": "true"}
            if title:
                form["title"] = title
            files = None
            if kind == "link":
                form["url"] = link
            else:
                files = {"file": (path.name, data, "application/octet-stream")}
            sent = await call("POST", "/sources", form=form, files=files)
            return SourceWrite(
                created=True, source_id=sent.get("id"), command_id=sent.get("command_id"),
                status=sent.get("status"), approved_by=approved_by,
                note="Saved. Open Notebook processes it in the background; check with open_notebook_source_get.",
                **base,
            )
