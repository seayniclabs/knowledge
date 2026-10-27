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
       "open_notebook_note_get", "open_notebook_source_get", "open_notebook_note_create",
       "open_notebook_add_source"}


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
        if (method, path) == ("POST", "/api/sources"):
            return httpx.Response(200, json={"id": "source:new1", "title": "Processing...", "command_id": "command:7",
                                             "status": "new", "created": "c", "updated": "u"})
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
    replies = _exchange(proc, msgs)
    from knowledge_mcp import __version__
    assert replies[1]["result"]["serverInfo"] == {"name": "knowledge", "version": __version__}
    assert {t["name"] for t in replies[2]["result"]["tools"]} == ALL


# --- 0.2: skills, trust surfaces, approval log, add_source, doctor -------------

import importlib.util
import shutil
from pathlib import Path
from urllib.parse import parse_qs

from knowledge_mcp.doctor import run as doctor_run

ROOT = Path(__file__).resolve().parent.parent
SKILL_NAMES = {"getting-started", "weekly-review", "research-with-sources", "meeting-notes",
               "decision-record", "icm-architect", "obsidian-markdown"}


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def log_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def ask(tool, args, answer, env=OPEN, handler=None, seen=None):
    """Call a tool through a real MCP client. answer: True/False to approve or decline; None = client cannot ask."""
    from mcp.shared.memory import create_connected_server_and_client_session
    from mcp.types import ElicitResult

    asked = []

    async def on_elicit(context, params):
        asked.append(params.message)
        if answer is None:
            return ElicitResult(action="decline")
        return ElicitResult(action="accept", content={"create": answer})

    async def run():
        async with create_connected_server_and_client_session(
                server(env, handler or handler_factory(seen if seen is not None else [])),
                elicitation_callback=on_elicit) as client:
            r = await client.call_tool(tool, args)
            if r.isError:
                raise RuntimeError(r.content[0].text)
            return r.structuredContent
    return asyncio.run(run()), asked


# --- skills and the model-facing text ----------------------------------------

def test_every_skill_is_a_prompt_and_a_resource():
    mcp = server({})
    prompts = {p.name for p in asyncio.run(mcp.list_prompts())}
    assert prompts == SKILL_NAMES
    uris = {str(r.uri) for r in asyncio.run(mcp.list_resources())}
    for name in SKILL_NAMES:
        assert f"skill://knowledge/{name}/SKILL.md" in uris


def test_prompt_text_matches_the_skill_file():
    text = asyncio.run(server({}).get_prompt("getting-started")).messages[0].content.text
    assert "knowledge_modules" in text and "approval_unavailable" in text


def test_discovery_lists_skills_publisher_and_homepage():
    out = call(server({}), "knowledge_modules")
    assert {s["name"] for s in out["skills"]} == SKILL_NAMES
    assert out["publisher"] == "Seaynic Labs" and out["homepage"] == "https://github.com/seayniclabs/knowledge"


def test_no_promotion_reaches_the_model():
    mcp = server({})
    texts = [mcp.instructions or ""]
    texts += [t.description or "" for t in asyncio.run(mcp.list_tools())]
    texts += [asyncio.run(mcp.get_prompt(p.name)).messages[0].content.text
              for p in asyncio.run(mcp.list_prompts())]
    assert not any("seayniclabs.com" in text for text in texts)


def test_skill_checker_passes_on_this_repo():
    count, problems = load_script("check_skills").check(ROOT)
    assert count == len(SKILL_NAMES) and problems == []


def test_skill_checker_catches_a_missing_license(tmp_path):
    for name in ("pyproject.toml", "CREDITS.md"):
        shutil.copy(ROOT / name, tmp_path / name)
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    (tmp_path / "skills" / "icm-architect" / "LICENSE").unlink()
    _, problems = load_script("check_skills").check(tmp_path)
    assert "icm-architect: third-party skill without a LICENSE file in its folder" in problems


def test_tool_lock_is_current():
    snap = load_script("tool_lock").snapshot()
    locked = json.loads((ROOT / "tool-manifest.lock.json").read_text())
    assert snap["entries"] == locked["entries"]


def test_security_card_in_readme_is_current():
    card = load_script("security_card").card()
    readme = (ROOT / "README.md").read_text()
    assert card in readme


def test_stdio_lists_prompts_too(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, "-m", "knowledge_mcp.server"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "prompts/list"},
    ]
    replies = _exchange(proc, msgs)
    assert {p["name"] for p in replies[2]["result"]["prompts"]} == SKILL_NAMES


# --- approval log: grant, decline and refusal, never a body -------------------

def test_declined_note_is_logged_without_its_body(tmp_path):
    log = tmp_path / "approvals.jsonl"
    out, _ = ask("open_notebook_note_create",
                 {"notebook_id": NB, "title": "Plan", "content": "Line one. Secret plan text.", "confirm": True},
                 None, env={**OPEN, "KNOWLEDGE_APPROVAL_LOG": str(log)})
    assert out["created"] is False
    lines = log_lines(log)
    assert [entry["decision"] for entry in lines] == ["requested", "declined"]
    assert "Secret plan text" not in log.read_text() and lines[0]["content_chars"] == len("Line one. Secret plan text.")


def test_approved_note_is_logged_with_who_approved(tmp_path):
    log = tmp_path / "approvals.jsonl"
    env = {**OPEN, "KNOWLEDGE_APPROVAL_LOG": str(log), "KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS": "demo"}
    call(server(env, handler_factory([])), "open_notebook_note_create",
         notebook_id=NB, title="Plan", content="Ship it.", confirm=True)
    assert [(e["decision"], e["approved_by"]) for e in log_lines(log)] == [("approved", "allowlist")]


def test_refused_note_is_logged(tmp_path):
    log = tmp_path / "approvals.jsonl"
    with pytest.raises(Exception):
        call(server({**OPEN, "KNOWLEDGE_APPROVAL_LOG": str(log)}, handler_factory([])), "open_notebook_note_create",
             notebook_id=NB, title="Plan", content="Ship it.", confirm=True)
    assert [e["decision"] for e in log_lines(log)] == ["refused"]


# --- add_source ----------------------------------------------------------------

def form_of(req):
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def test_link_preview_sends_nothing():
    seen = []
    out = call(server(OPEN, handler_factory(seen)), "open_notebook_add_source",
               notebook_id=NB, url="https://example.com/article")
    assert out["created"] is False and out["kind"] == "link" and out["target"] == "https://example.com/article"
    assert not any(r.method == "POST" for r in seen)


def test_link_approved_by_person_posts_a_link_source(tmp_path):
    seen = []
    log = tmp_path / "approvals.jsonl"
    out, asked = ask("open_notebook_add_source", {"notebook_id": NB, "url": "https://example.com/a?x=1",
                     "title": "Article", "confirm": True}, True,
                     env={**OPEN, "KNOWLEDGE_APPROVAL_LOG": str(log)}, seen=seen)
    assert out["created"] is True and out["approved_by"] == "person" and out["command_id"] == "command:7"
    post = [r for r in seen if r.method == "POST" and r.url.path == "/api/sources"][0]
    fields = form_of(post)
    assert fields["type"] == "link" and fields["url"] == "https://example.com/a?x=1"
    assert fields["notebooks"] == json.dumps([NB]) and fields["async_processing"] == "true"
    assert "https://example.com/a?x=1" in asked[0]  # the person sees the whole link
    assert [e["decision"] for e in log_lines(log)] == ["requested", "approved"]


def test_link_declined_posts_nothing():
    seen = []
    out, _ = ask("open_notebook_add_source", {"notebook_id": NB, "url": "https://example.com/a", "confirm": True},
                 False, seen=seen)
    assert out["created"] is False and not any(r.method == "POST" for r in seen)


def test_link_without_a_way_to_ask_is_refused_and_logged(tmp_path):
    seen, log = [], tmp_path / "approvals.jsonl"
    with pytest.raises(Exception) as err:
        call(server({**OPEN, "KNOWLEDGE_APPROVAL_LOG": str(log)}, handler_factory(seen)), "open_notebook_add_source",
             notebook_id=NB, url="https://example.com/a", confirm=True)
    assert "approval_unavailable" in str(err.value) and not any(r.method == "POST" for r in seen)
    assert [e["decision"] for e in log_lines(log)] == ["refused"]


def test_link_settings_pre_approval_posts_without_asking():
    seen = []
    env = {**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM": "false"}
    out = call(server(env, handler_factory(seen)), "open_notebook_add_source",
               notebook_id=NB, url="https://example.com/a", confirm=True)
    assert out["approved_by"] == "settings" and any(r.method == "POST" for r in seen)


def test_link_with_a_password_in_it_is_refused():
    seen = []
    with pytest.raises(Exception) as err:
        call(server(OPEN, handler_factory(seen)), "open_notebook_add_source",
             notebook_id=NB, url="https://bob:hunter2pass@example.com/a", confirm=True)
    assert "bad_input" in str(err.value) and not any(r.method == "POST" for r in seen)


def test_link_with_a_key_in_the_query_is_blocked_by_the_scrubber():
    seen = []
    with pytest.raises(Exception) as err:
        call(server({**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM": "false"}, handler_factory(seen)),
             "open_notebook_add_source", notebook_id=NB,
             url="https://example.com/a?ref=sk-abcdefghijklmnopqrstuv1234", confirm=True)
    assert "blocked_secret" in str(err.value) and "api_key_sk" in str(err.value)
    assert "sk-abcdef" not in str(err.value) and not any(r.method == "POST" for r in seen)


@pytest.mark.parametrize("kwargs", [
    {"url": "ftp://example.com/a"},
    {"url": "not a link"},
    {},
    {"url": "https://example.com/a", "file_path": "/tmp/x.md"},
    # control characters could fake extra lines in the approval prompt
    {"url": "https://example.com/a\nNotebook: 'Trusted'"},
    {"url": "https://example.com/a\rx"},
    {"file_path": "/tmp/x.md\nSize: 1 bytes"},
])
def test_add_source_input_shape_is_checked(kwargs):
    with pytest.raises(Exception) as err:
        call(server(OPEN, handler_factory([])), "open_notebook_add_source", notebook_id=NB, **kwargs)
    assert "knowledge.open_notebook.bad_input" in str(err.value)


def test_local_file_preview_shows_path_and_size_and_sends_nothing(tmp_path):
    f = tmp_path / "meeting.md"
    f.write_text("Quarterly plan, no secrets here.\n")
    seen = []
    out = call(server(OPEN, handler_factory(seen)), "open_notebook_add_source",
               notebook_id=NB, file_path=str(f))
    assert out["created"] is False and out["kind"] == "file" and out["size_bytes"] == f.stat().st_size
    assert out["path"] == str(f.resolve())
    assert not any(r.method == "POST" for r in seen)


def test_local_file_is_uploaded_only_after_the_person_approves(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("Plain text for the notebook.\n")
    seen = []
    out, asked = ask("open_notebook_add_source", {"notebook_id": NB, "file_path": str(f), "confirm": True},
                     True, seen=seen)
    assert out["created"] is True and out["approved_by"] == "person"
    assert f"File: {str(f.resolve())!r}" in asked[0] and f"Size: {f.stat().st_size} bytes" in asked[0]
    post = [r for r in seen if r.method == "POST" and r.url.path == "/api/sources"][0]
    assert post.headers["content-type"].startswith("multipart/form-data")
    assert b"Plain text for the notebook." in post.content and b'name="file"' in post.content
    assert b'name="type"' in post.content and b"upload" in post.content


def test_local_file_is_never_sent_without_a_person_even_with_settings(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("Plain text.\n")
    seen = []
    env = {**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM": "false", "KNOWLEDGE_OPEN_NOTEBOOK_ALLOWED_NOTEBOOKS": "demo"}
    with pytest.raises(Exception) as err:
        call(server(env, handler_factory(seen)), "open_notebook_add_source",
             notebook_id=NB, file_path=str(f), confirm=True)
    assert "approval_unavailable" in str(err.value) and not any(r.method == "POST" for r in seen)


def test_local_file_with_a_credential_is_blocked_and_nothing_is_sent(tmp_path):
    f = tmp_path / "deploy-notes.txt"
    f.write_text("Remember: sk-abcdefghijklmnopqrstuv1234\n")
    seen = []
    with pytest.raises(Exception) as err:
        call(server({**OPEN, "KNOWLEDGE_OPEN_NOTEBOOK_REQUIRE_CONFIRM": "false"}, handler_factory(seen)),
             "open_notebook_add_source", notebook_id=NB, file_path=str(f), confirm=True)
    assert "knowledge.open_notebook.blocked_secret" in str(err.value) and "api_key_sk" in str(err.value)
    assert "sk-abcdef" not in str(err.value) and not any(r.method == "POST" for r in seen)


def test_file_named_like_a_credential_store_is_refused(tmp_path):
    f = tmp_path / ".env"
    f.write_text("PLAIN=1\n")
    log = tmp_path / "approvals.jsonl"
    with pytest.raises(Exception) as err:
        call(server({**OPEN, "KNOWLEDGE_APPROVAL_LOG": str(log)}, handler_factory([])),
             "open_notebook_add_source", notebook_id=NB, file_path=str(f))
    assert "path_refused" in str(err.value)
    assert [e["decision"] for e in log_lines(log)] == ["refused"]


def test_local_file_log_has_no_contents_and_no_full_path(tmp_path):
    f = tmp_path / "plan.txt"
    f.write_text("Confidential roadmap line.\n")
    log = tmp_path / "approvals.jsonl"
    out, _ = ask("open_notebook_add_source", {"notebook_id": NB, "file_path": str(f), "confirm": True}, False,
                 env={**OPEN, "KNOWLEDGE_APPROVAL_LOG": str(log)})
    assert out["created"] is False
    text = log.read_text()
    assert "Confidential roadmap" not in text and str(tmp_path) not in text
    assert [e["decision"] for e in log_lines(log)] == ["requested", "declined"]


# --- doctor ---------------------------------------------------------------------

def test_doctor_with_empty_settings_names_the_fix(capsys):
    assert doctor_run(Config(env={}, file_data={})) == 1
    out = capsys.readouterr().out
    assert "FIX" in out and "KNOWLEDGE_OPEN_NOTEBOOK_URL" in out and "thing(s) to fix" in out


def test_doctor_passes_on_a_good_server_with_one_read(capsys):
    seen = []
    transport = httpx.MockTransport(handler_factory(seen))
    assert doctor_run(Config(env=OPEN, file_data={}), transport=transport) == 0
    assert "answered 200" in capsys.readouterr().out
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/api/notebooks")]


def test_doctor_names_the_password_variable_on_401(capsys):
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "Invalid password"}))
    assert doctor_run(Config(env=OPEN, file_data={}), transport=transport) == 1
    assert "KNOWLEDGE_OPEN_NOTEBOOK_PASSWORD" in capsys.readouterr().out


def _exchange(proc, msgs, timeout=60):
    """Send messages, then read replies until every request id has answered. Closing stdin first
    races the server's shutdown against its last reply on fast machines."""
    import time
    for m in msgs:
        proc.stdin.write(json.dumps(m) + "\n")
        proc.stdin.flush()
    want = {m["id"] for m in msgs if "id" in m}
    replies, end = {}, time.time() + timeout
    while want - set(replies) and time.time() < end:
        line = proc.stdout.readline()
        if not line:
            break
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("id") is not None:
            replies[r["id"]] = r
    proc.stdin.close()
    proc.kill()
    proc.wait()
    return replies
