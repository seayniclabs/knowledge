"""Start-and-list, module switches, missing config, mocked calls, approvals, scrubber, stdio.

No test touches the network: every HTTP call goes through httpx.MockTransport.
Example values only (example.com, made-up ids).
"""

import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest

from knowledge_mcp.config import Config
from knowledge_mcp.scrub import find_credential_kinds
from knowledge_mcp.server import build_server

BASE = "https://notebook.example.com"
OPEN = {"KNOWLEDGE_OPEN_NOTEBOOK_URL": BASE}
NB = "notebook:demo1"
NOTE = "note:n1"
SRC = "source:s1"

ALL = {"knowledge_modules", "open_notebook_notebooks", "open_notebook_search", "open_notebook_notes",
       "open_notebook_note_get", "open_notebook_source_get", "open_notebook_note_create"}


def server(env, handler=None):
    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler or (lambda r: httpx.Response(500))))
    return build_server(Config(env=env, file_data={}), client_factory=factory)


def tools(mcp):
    return {t.name for t in asyncio.run(mcp.list_tools())}


def call(mcp, name, **args):
    """Return the structured result, or raise the tool's error."""
    _, structured = asyncio.run(mcp.call_tool(name, args))
    return structured


# --- start, list, switches --------------------------------------------------

def test_empty_env_starts_and_lists_every_tool():
    assert tools(server({})) == ALL


def test_every_tool_has_an_output_schema():
    assert all(t.outputSchema for t in asyncio.run(server({}).list_tools()))


def test_module_switch_off_hides_its_tools():
    assert tools(server({"KNOWLEDGE_OPEN_NOTEBOOK_ENABLED": "false"})) == {"knowledge_modules"}


def test_config_file_is_read_and_env_wins():
    cfg = Config(env={"KNOWLEDGE_OPEN_NOTEBOOK_URL": "https://from-env.example.com"},
                 file_data={"open_notebook": {"url": "https://from-file.example.com", "password": "p"}})
    assert cfg.get("open_notebook", "url") == "https://from-env.example.com"
    assert cfg.get("open_notebook", "password") == "p"


def test_knowledge_modules_reports_missing_settings():
    out = call(server({}), "knowledge_modules")
    mod = out["modules"][0]
    assert mod["name"] == "open_notebook" and mod["configured"] is False
    assert mod["missing"] == ["KNOWLEDGE_OPEN_NOTEBOOK_URL"]


def test_knowledge_modules_detail_never_leaks_the_password():
    env = {**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_PASSWORD": "hunter-two-hunter"}
    out = call(server(env), "knowledge_modules", module="open_notebook")
    assert "hunter-two-hunter" not in json.dumps(out)
    flags = {s["env_var"]: s["set"] for s in out["modules"][0]["settings"]}
    assert flags["KNOWLEDGE_OPEN_NOTEBOOK_PASSWORD"] is True


# --- missing config never crashes -------------------------------------------

@pytest.mark.parametrize("tool,args", [
    ("open_notebook_notebooks", {}),
    ("open_notebook_search", {"query": "x"}),
    ("open_notebook_notes", {"notebook_id": NB}),
    ("open_notebook_note_get", {"note_id": NOTE}),
    ("open_notebook_source_get", {"source_id": SRC}),
    ("open_notebook_note_create", {"notebook_id": NB, "title": "t", "content": "c"}),
])
def test_unconfigured_tool_names_missing_variable(tool, args):
    with pytest.raises(Exception) as err:
        call(server({}), tool, **args)
    assert "knowledge.open_notebook.not_configured" in str(err.value)
    assert "KNOWLEDGE_OPEN_NOTEBOOK_URL" in str(err.value)


def test_bad_url_scheme_is_refused():
    with pytest.raises(Exception) as err:
        call(server({"KNOWLEDGE_OPEN_NOTEBOOK_URL": "ftp://notebook.example.com"}), "open_notebook_notebooks")
    assert "bad_url" in str(err.value)


# --- mocked Open Notebook API -------------------------------------------------

def handler_factory(seen, overrides=None):
    notebooks = [
        {"id": "notebook:a", "name": "Research", "description": "", "archived": False,
         "source_count": 2, "note_count": 1, "updated": "2026-10-01"},
        {"id": "notebook:b", "name": "Inbox", "description": "", "archived": False,
         "source_count": 0, "note_count": 0, "updated": "2026-09-01"},
        {"id": NB, "name": "Demo", "description": "", "archived": False,
         "source_count": 0, "note_count": 0, "updated": "2026-08-01"},
    ]

    def handle(req: httpx.Request):
        seen.append(req)
        path, method = req.url.path, req.method
        if overrides and (method, path) in overrides:
            return overrides[(method, path)](req)
        if (method, path) == ("GET", "/api/notebooks"):
            return httpx.Response(200, json=notebooks)
        if (method, path) == ("POST", "/api/search"):
            body = json.loads(req.content)
            hits = [{"id": f"source:h{i}", "title": f"Doc {i}", "content": "a `match` here",
                     "parent_id": f"source:h{i}", "relevance": 1.0 - i / 10} for i in range(body["limit"])]
            return httpx.Response(200, json={"results": hits, "total_count": len(hits), "search_type": body["type"]})
        if (method, path) == ("GET", "/api/notes"):
            return httpx.Response(200, json=[
                {"id": "note:n1", "title": "First", "content": None, "note_type": "human", "updated": "t1"},
                {"id": "note:n2", "title": "Second", "content": None, "note_type": "ai", "updated": "t2"},
                {"id": "note:n3", "title": "Third", "content": None, "note_type": "human", "updated": "t3"},
            ])
        if (method, path) == ("GET", "/api/notes/note:n1"):
            return httpx.Response(200, json={"id": "note:n1", "title": "First", "content": "x" * 900,
                                             "note_type": "human", "created": "c", "updated": "u"})
        if (method, path) == ("GET", "/api/sources/source:s1"):
            return httpx.Response(200, json={"id": "source:s1", "title": "Paper", "topics": ["ai"],
                                             "full_text": "y" * 10, "embedded": True, "embedded_chunks": 3,
                                             "notebooks": ["notebook:a"], "status": "completed",
                                             "created": "c", "updated": "u"})
        if (method, path) == ("POST", "/api/notes"):
            return httpx.Response(200, json={"id": "note:new", "title": "t", "content": "c",
                                             "note_type": "human", "created": "c", "updated": "u",
                                             "command_id": "command:9"})
        return httpx.Response(404, json={"detail": "Not found here"})
    return handle


def test_notebooks_paged_and_archived_filter_sent():
    seen = []
    out = call(server(OPEN, handler_factory(seen)), "open_notebook_notebooks", limit=2)
    assert [n["name"] for n in out["notebooks"]] == ["Research", "Inbox"]
    assert out["has_more"] is True and out["next_offset"] == 2
    assert seen[-1].url.params["archived"] == "false"
    out2 = call(server(OPEN, handler_factory([])), "open_notebook_notebooks", limit=2, offset=2)
    assert [n["name"] for n in out2["notebooks"]] == ["Demo"] and out2["has_more"] is False


def test_password_sent_as_bearer_only_when_set():
    seen = []
    call(server({**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_PASSWORD": "pw-123"}, handler_factory(seen)), "open_notebook_notebooks")
    assert seen[0].headers["authorization"] == "Bearer pw-123"
    seen2 = []
    call(server(OPEN, handler_factory(seen2)), "open_notebook_notebooks")
    assert "authorization" not in seen2[0].headers


def test_search_text_mode_sends_expected_body_and_pages():
    seen = []
    out = call(server(OPEN, handler_factory(seen)), "open_notebook_search", query="  match  ", limit=2, offset=1)
    body = json.loads(seen[-1].content)
    assert body == {"query": "match", "type": "text", "limit": 4, "search_sources": True, "search_notes": True}
    assert [h["id"] for h in out["hits"]] == ["source:h1", "source:h2"]
    assert out["hits"][0]["kind"] == "source" and out["hits"][0]["score"] == pytest.approx(0.9)
    assert out["has_more"] is True and out["next_offset"] == 3


def test_search_vector_scoped_to_one_notebook():
    seen = []
    out = call(server(OPEN, handler_factory(seen)), "open_notebook_search", query="ideas", mode="vector",
               notebook_id=NB, limit=1)
    body = json.loads(seen[-1].content)
    assert body["type"] == "vector" and body["notebook_ids"] == [NB]
    assert out["mode"] == "vector" and len(out["hits"]) == 1


def test_search_rejects_bad_ids_and_empty_query():
    mcp = server(OPEN, handler_factory([]))
    with pytest.raises(Exception) as err:
        call(mcp, "open_notebook_search", query="x", notebook_id="notebook:../../etc")
    assert "bad_input" in str(err.value)
    with pytest.raises(Exception) as err:
        call(mcp, "open_notebook_search", query="   ")
    assert "bad_input" in str(err.value)


def test_notes_list_is_paged_titles_only():
    out = call(server(OPEN, handler_factory([])), "open_notebook_notes", notebook_id=NB, limit=2)
    assert [n["title"] for n in out["notes"]] == ["First", "Second"]
    assert out["next_offset"] == 2


def test_note_get_truncates_long_content():
    out = call(server(OPEN, handler_factory([])), "open_notebook_note_get", note_id=NOTE, max_chars=200)
    assert out["truncated"] is True and len(out["content"]) == 200 and out["content_chars"] == 900


def test_note_get_rejects_path_tricks():
    with pytest.raises(Exception) as err:
        call(server(OPEN, handler_factory([])), "open_notebook_note_get", note_id="note:x/../../api/notebooks")
    assert "bad_input" in str(err.value)


def test_source_get_reads_text_and_notebooks():
    out = call(server(OPEN, handler_factory([])), "open_notebook_source_get", source_id=SRC)
    assert out["topics"] == ["ai"] and out["notebooks"] == ["notebook:a"] and out["truncated"] is False


def test_not_found_is_readable():
    missing = {("GET", "/api/notes"): lambda r: httpx.Response(404, json={"detail": "Notebook not found"})}
    with pytest.raises(Exception) as err:
        call(server(OPEN, handler_factory([], overrides=missing)), "open_notebook_notes", notebook_id="notebook:missing")
    assert "knowledge.open_notebook.not_found" in str(err.value)


def test_auth_failure_names_the_password_variable():
    with pytest.raises(Exception) as err:
        call(server(OPEN, lambda r: httpx.Response(401, json={"detail": "Invalid password"})), "open_notebook_notebooks")
    assert "knowledge.open_notebook.auth_failed" in str(err.value)
    assert "KNOWLEDGE_OPEN_NOTEBOOK_PASSWORD" in str(err.value)


def test_non_json_response_is_explained():
    with pytest.raises(Exception) as err:
        call(server(OPEN, lambda r: httpx.Response(200, text="<html>")), "open_notebook_notebooks")
    assert "did not return JSON" in str(err.value)


# --- writes: preview, approval, allowlist, scrubber ----------------------------

def test_note_create_previews_without_confirm_and_writes_nothing():
    seen = []
    out = call(server(OPEN, handler_factory(seen)), "open_notebook_note_create",
               notebook_id=NB, title="Plan", content="Ship it on Friday.")
    assert out["created"] is False and out["notebook_name"] == "Demo" and out["preview"] == "Ship it on Friday."
    assert not any(r.method == "POST" for r in seen)


def test_confirm_alone_cannot_write_without_a_person():
    seen = []
    with pytest.raises(Exception) as err:
        call(server(OPEN, handler_factory(seen)), "open_notebook_note_create",
             notebook_id=NB, title="Plan", content="Ship it.", confirm=True)
    assert "approval_unavailable" in str(err.value)
    assert not any(r.method == "POST" for r in seen)


def test_allowlist_approves_listed_notebook_only():
    seen = []
    env = {**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS": "demo, inbox"}
    out = call(server(env, handler_factory(seen)), "open_notebook_note_create",
               notebook_id=NB, title="Plan", content="Ship it.", confirm=True)
    assert out["created"] is True and out["approved_by"] == "allowlist" and out["embedding_command_id"] == "command:9"
    post = [r for r in seen if r.method == "POST"][0]
    assert json.loads(post.content) == {"title": "Plan", "content": "Ship it.", "note_type": "human", "notebook_id": NB}


def test_allowlist_does_not_cover_other_notebooks():
    seen = []
    env = {**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS": "research"}
    with pytest.raises(Exception) as err:
        call(server(env, handler_factory(seen)), "open_notebook_note_create",
             notebook_id=NB, title="Plan", content="Ship it.", confirm=True)
    assert "approval_unavailable" in str(err.value) and not any(r.method == "POST" for r in seen)


def test_require_confirm_false_lets_assistant_write():
    seen = []
    env = {**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM": "false"}
    out = call(server(env, handler_factory(seen)), "open_notebook_note_create",
               notebook_id=NB, title="Plan", content="Ship it.", confirm=True)
    assert out["approved_by"] == "settings" and any(r.method == "POST" for r in seen)


def send_via_client(answer, env=OPEN):
    """Call open_notebook_note_create through a real MCP client that answers the approval prompt."""
    from mcp.shared.memory import create_connected_server_and_client_session
    from mcp.types import ElicitResult

    seen, asked = [], []

    async def on_elicit(context, params):
        asked.append(params.message)
        if answer is None:
            return ElicitResult(action="decline")
        return ElicitResult(action="accept", content={"create": answer})

    async def run():
        async with create_connected_server_and_client_session(
                server(env, handler_factory(seen)), elicitation_callback=on_elicit) as client:
            r = await client.call_tool("open_notebook_note_create",
                                       {"notebook_id": NB, "title": "Plan", "content": "Line one.\nLine two.",
                                        "confirm": True})
            return r.structuredContent
    return asyncio.run(run()), seen, asked


def test_person_approves_through_the_client():
    out, seen, asked = send_via_client(True)
    assert out["created"] is True and out["approved_by"] == "person"
    assert any(r.method == "POST" for r in seen)
    # The person sees the notebook, the title and the whole text.
    for part in ("'Demo'", "'Plan'", "Line one.\nLine two."):
        assert part in asked[0]


def test_person_declines_through_the_client():
    out, seen, _ = send_via_client(None)
    assert out["created"] is False and not any(r.method == "POST" for r in seen)


@pytest.mark.parametrize("text,kind", [
    ("token sk-abcdefghijklmnopqrstuv1234", "api_key_sk"),
    ("key ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github_token"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIB", "private_key"),
    ("connect to https://bob:hunter2pass@db.example.com/x", "password_in_url"),
    ("password: correct-horse-battery", "credential_assignment"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345", "bearer_token"),
])
def test_scrubber_names_the_kind_not_the_value(text, kind):
    assert kind in find_credential_kinds(text)
    seen = []
    with pytest.raises(Exception) as err:
        call(server({**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM": "false"}, handler_factory(seen)),
             "open_notebook_note_create", notebook_id=NB, title="Notes", content=text, confirm=True)
    message = str(err.value)
    assert "knowledge.open_notebook.blocked_secret" in message and kind in message
    assert "sk-abcdef" not in message and "ghp_abc" not in message and "hunter2" not in message
    assert not any(r.method == "POST" for r in seen)


def test_scrubber_passes_ordinary_text():
    assert find_credential_kinds("Meeting notes: discuss the API design and the key risks.") == []


# --- real process over stdio, empty environment -----------------------------

def test_stdio_process_starts_with_empty_env(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, "-m", "knowledge_mcp.server"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    out, _ = proc.communicate("".join(json.dumps(m) + "\n" for m in msgs), timeout=60)
    replies = {r.get("id"): r for r in map(json.loads, out.splitlines()) if r.get("id")}
    from knowledge_mcp import __version__
    assert replies[1]["result"]["serverInfo"] == {"name": "knowledge", "version": __version__}
    assert {t["name"] for t in replies[2]["result"]["tools"]} == ALL
