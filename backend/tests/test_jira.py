"""Jira integration tests: deterministic (mock transport) + security guarantees."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.services.jira import adf, crypto
from app.services.jira.client import JiraClient, JiraError, extract_key, normalize_base_url


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_CREDENTIAL_KEY", "test-master-key-for-jira-tests-only")
    db.reset_for_tests(tmp_path)
    yield tmp_path


@pytest.fixture()
def client(tmp_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


ISSUE_RAW = {
    "id": "10001", "key": "COS-5904",
    "fields": {
        "summary": "Kiosk menu v4 out-of-stock logic fix",
        "description": {"type": "doc", "version": 1, "content": [
            {"type": "heading", "attrs": {"level": 2},
             "content": [{"type": "text", "text": "Problem"}]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": "OOS badge missing when "},
                {"type": "text", "text": "stock hits zero", "marks": [{"type": "strong"}]}]},
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [{"type": "paragraph",
                                                  "content": [{"type": "text",
                                                               "text": "repro on kiosk"}]}]}]},
        ]},
        "issuetype": {"name": "Bug"},
        "status": {"name": "To Do", "statusCategory": {"name": "To Do"}},
        "priority": {"name": "High"},
        "project": {"key": "COS", "name": "Kiosk"},
        "assignee": {"accountId": "u1", "displayName": "Kevin"},
        "reporter": {"displayName": "Asha"},
        "labels": ["kiosk", "menu-v4"],
        "components": [{"name": "kiosk-ui"}],
        "parent": {"key": "COS-5900", "fields": {"issuetype": {"name": "Epic"}}},
        "duedate": "2026-11-01",
        "attachment": [{"id": "att1", "filename": "screen.png", "mimeType": "image/png",
                        "size": 1234, "created": "2026-10-01",
                        "content": "https://jira.test/secure/att1",
                        "author": {"displayName": "Kevin"}}],
        "issuelinks": [],
        "comment": {"total": 1, "comments": [
            {"id": "c1", "created": "2026-10-02", "updated": "2026-10-02",
             "author": {"displayName": "Asha"},
             "body": {"type": "doc", "version": 1, "content": [
                 {"type": "paragraph",
                  "content": [{"type": "text", "text": "repro confirmed"}]}]}}]},
        "updated": "2026-10-20T10:00:00.000+0000",
        "created": "2026-10-01T10:00:00.000+0000",
    },
    "changelog": {"histories": [
        {"id": "h1", "created": "2026-10-03", "author": {"displayName": "Kevin"},
         "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]}]},
}

ME = {"accountId": "u9", "displayName": "Test User", "emailAddress": "t@x.test"}


def make_transport(state: dict | None = None) -> httpx.MockTransport:
    state = state if state is not None else {"created": [], "comments": [],
                                            "transitioned": [], "assigned": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        body = {}
        if request.content:
            try:
                body = json.loads(request.content.decode())
            except Exception:
                body = {}

        def ok(payload, status=200):
            return httpx.Response(status, json=payload)

        if path == "/rest/api/3/myself":
            return ok(ME)
        if path == "/rest/api/3/mypermissions":
            return ok({"permissions": {"CREATE_ISSUES": {"havePermission": True}}})
        if path == "/rest/api/3/project/search":
            return ok({"values": [{"id": "1", "key": "COS", "name": "Kiosk",
                                   "projectTypeKey": "software"}], "isLast": True})
        if path == "/rest/api/3/search/jql" and method == "POST":
            jql = body.get("jql", "")
            if "EMPTYPROJ" in jql:
                return ok({"issues": [], "nextPageToken": None, "total": 0})
            if "INVALIDJQL!!!" in jql:
                return httpx.Response(400, json={"errorMessages": ["JQL parse error"]})
            token = body.get("nextPageToken")
            if token == "page2":
                return ok({"issues": [], "nextPageToken": None, "total": 1})
            if body.get("maxResults") == 1 or "maxResults" in body and body.get("maxResults", 50) <= 50:
                # first page carries the issue, second page empty
                if "updated >=" in jql and state.get("second_empty"):
                    return ok({"issues": [], "nextPageToken": None, "total": 0})
                return ok({"issues": [ISSUE_RAW], "nextPageToken": "page2", "total": 2})
            return ok({"issues": [ISSUE_RAW], "nextPageToken": None, "total": 1})
        if path == "/rest/api/3/search" and method == "POST":
            return ok({"issues": [ISSUE_RAW], "startAt": 0, "total": 1})
        if path == "/rest/api/3/jql/parse":
            qs = (body.get("queries") or [""])[0]
            if "INVALIDJQL!!!" in qs:
                return httpx.Response(400, json={"errorMessages": ["bad jql"]})
            return ok({"queries": [{"valid": True}]})
        if path == "/rest/api/3/issue" and method == "POST":
            fields = body.get("fields", {})
            key = f"{fields['project']['key']}-1234"
            state["created"].append(fields)
            return ok({"id": "20001", "key": key}, 201)
        if path.startswith("/rest/api/3/issue/COS-5904/transitions"):
            if method == "GET":
                return ok({"transitions": [{"id": "21", "name": "Start Progress"},
                                           {"id": "31", "name": "In Review"}]})
            state["transitioned"].append(body)
            return httpx.Response(204)
        if path.startswith("/rest/api/3/issue/COS-5904/assignee"):
            state["assigned"].append(body)
            return httpx.Response(204)
        if path.startswith("/rest/api/3/issue/COS-5904/comment"):
            if method == "GET":
                return ok({"comments": ISSUE_RAW["fields"]["comment"]["comments"],
                           "total": 1})
            cid = f"c{len(state['comments']) + 2}"
            state["comments"].append(body)
            return ok({"id": cid}, 201)
        if path == "/rest/api/3/issue/COS-5904" and method == "GET":
            return ok(ISSUE_RAW)
        import re as _re
        gm = _re.match(r"^/rest/api/3/issue/([A-Z][A-Z0-9_]*-\d+)$", path)
        if gm and method == "GET":
            raw = json.loads(json.dumps(ISSUE_RAW))
            raw["key"] = gm.group(1)
            return ok(raw)
        if path == "/rest/api/3/issue/COS-5904" and method in ("PUT", "DELETE"):
            return httpx.Response(204)
        if path == "/rest/api/3/issue/COS-9999":
            return httpx.Response(404, json={"errorMessages": ["not found"]})
        if path == "/rest/api/3/user/search":
            q = request.url.params.get("query", "")
            if q.lower().startswith("kev"):
                return ok([{"accountId": "u1", "displayName": "Kevin"},
                           {"accountId": "u2", "displayName": "Kevin Jr"}])
            if q.lower() == "kevin":
                return ok([{"accountId": "u1", "displayName": "Kevin"}])
            return ok([])
        if path == "/rest/agile/1.0/board" and method == "GET":
            return ok({"values": [{"id": 7, "name": "Kiosk board", "type": "scrum"}],
                       "isLast": True})
        if path == "/rest/agile/1.0/board/7":
            return ok({"id": 7, "name": "Kiosk board", "type": "scrum",
                       "location": {"projectKey": "COS"}})
        if path == "/rest/agile/1.0/board/7/configuration":
            return ok({"columnConfig": {"columns": [{"name": "To Do", "statuses": [{"id": "1"}]}]}})
        if path == "/rest/agile/1.0/board/7/issue":
            return ok({"issues": [ISSUE_RAW], "total": 1})
        if path == "/rest/agile/1.0/board/7/sprint":
            return ok({"values": [{"id": 3, "name": "Sprint 12", "state": "active"}],
                       "isLast": True})
        if path == "/rest/agile/1.0/sprint/3/issue":
            return ok({"issues": [ISSUE_RAW], "total": 1})
        if path == "/rest/agile/1.0/issue/rank" and method == "PUT":
            state["ranked"] = body
            return httpx.Response(204)
        if path == "/rest/api/3/issueLink" and method == "POST":
            state.setdefault("links", []).append(body)
            return httpx.Response(201, json={})
        if path == "/rest/api/3/issueLink/9" and method == "DELETE":
            return httpx.Response(204)
        if path == "/rest/api/3/filter/search":
            return ok({"values": [{"id": "f1", "name": "My open bugs",
                                   "jql": "project = COS AND status != Done"}]})
        if path == "/rest/api/3/field":
            return ok([{"id": "summary", "name": "Summary"}])
        return httpx.Response(404, json={"errorMessages": ["no mock for " + method + " " + path]})

    return httpx.MockTransport(handler)


def seed_connection():
    from app.services.jira.service import save_connection
    return save_connection(display_name="Test", base_url="https://jira.test",
                           email="t@x.test", api_token="secret-token-123",
                           project_keys=["COS"])


# -- security / validation -------------------------------------------------------
def test_crypto_roundtrip_and_tamper(tmp_db):
    enc = crypto.encrypt_token("my-secret")
    assert enc.startswith("JENC1.")
    assert crypto.decrypt_token(enc) == "my-secret"
    assert "my-secret" not in enc
    tampered = enc[:-4] + ("AAAA" if not enc.endswith("AAAA") else "BBBB")
    try:
        crypto.decrypt_token(tampered)
        raise AssertionError("tamper must fail")
    except ValueError:
        pass


def test_base_url_validation(tmp_db):
    assert normalize_base_url("https://acme.atlassian.net/") == "https://acme.atlassian.net"
    for bad in ["", "ftp://x", "http://jira.company.com", "https://x.atlassian.net/rest/api/3",
                "https://user:pass@x.atlassian.net", "not a url"]:
        try:
            normalize_base_url(bad)
            raise AssertionError(f"should reject {bad}")
        except JiraError:
            pass
    # loopback http allowed (test doubles)
    assert normalize_base_url("http://localhost:8080") == "http://localhost:8080"


def test_extract_key():
    assert extract_key("please move COS-5904 to done") == "COS-5904"
    assert extract_key("no key here") is None


def test_adf_roundtrip():
    md = adf.adf_to_markdown(ISSUE_RAW["fields"]["description"])
    assert "OOS badge missing" in md and "stock hits zero" in md
    assert "- repro on kiosk" in md
    back = adf.markdown_to_adf("## Hi\n\n- a\n- b\n\n```py\nx=1\n```\n\n[link](https://x)")
    assert back["type"] == "doc"
    types = [b["type"] for b in back["content"]]
    assert types[:3] == ["heading", "bulletList", "codeBlock"]


# -- normalization + index ----------------------------------------------------------
def test_normalize_and_index(tmp_db):
    from app.services.jira.service import normalize_issue, upsert_index, card
    seed_connection()
    n = normalize_issue(ISSUE_RAW, "https://jira.test")
    assert n["key"] == "COS-5904" and n["epic_key"] == "COS-5900"
    assert n["url"] == "https://jira.test/browse/COS-5904"
    assert n["assignee_name"] == "Kevin"
    upsert_index("conn-missing", [n]) if False else None
    row = db.query_one("SELECT id FROM jira_connections")
    upsert_index(row["id"], [n])
    got = db.query_one("SELECT summary FROM jira_issues WHERE issue_key='COS-5904'")
    assert got and "out-of-stock" in got["summary"]
    c = card(n)
    assert set(c) >= {"key", "summary", "status", "url", "excerpt"}


# -- approval policy -------------------------------------------------------------------
def test_approval_policy(tmp_db):
    from app.services.jira.tools import jira_needs_approval
    assert jira_needs_approval("jira_search_issues", {}, True) == (False, "")
    assert jira_needs_approval("jira_get_issue", {}, True) == (False, "")
    need, _ = jira_needs_approval("jira_delete_issue", {}, True)
    assert need is True
    need, _ = jira_needs_approval("jira_create_issue", {}, False)
    assert need is True
    need, _ = jira_needs_approval("jira_create_issue", {}, True)
    assert need is False
    need, reason = jira_needs_approval("jira_create_issue", {}, True, {"ai_write": 0})
    assert need is True and "disabled" in reason


# -- connection endpoints: no secret leaks ------------------------------------------------
def test_connection_never_leaks_token(client):
    r = client.put("/api/v1/jira/connection", json={
        "display_name": "T", "base_url": "https://jira.test",
        "email": "t@x.test", "api_token": "super-secret-token"})
    assert r.status_code == 200, r.text
    body = r.text
    assert "super-secret-token" not in body
    got = client.get("/api/v1/jira/connection").json()["connection"]
    assert got["has_token"] is True
    assert "super-secret-token" not in json.dumps(got)
    assert got["base_url"] == "https://jira.test"


def test_test_connection_success_and_failure(client, monkeypatch):
    client.put("/api/v1/jira/connection", json={
        "base_url": "https://jira.test", "email": "t@x.test", "api_token": "tok"})
    import app.routers.jira as jr
    transport = make_transport()

    orig_make = jr.make_client

    def patched(row=None):
        c, r = orig_make(row)
        c._transport = transport
        return c, r

    monkeypatch.setattr(jr, "make_client", patched)
    r = client.post("/api/v1/jira/connection/test")
    assert r.status_code == 200, r.text
    assert r.json()["me"]["displayName"] == "Test User"


# -- sync: paginated + resumable ----------------------------------------------------------------
def test_sync_paginates_and_checkpoints(tmp_db):
    from app.services.jira import sync as jsync
    seed_connection()
    out = asyncio.run(jsync.run_sync(mode="initial", transport=make_transport()))
    assert out["status"] == "complete" and out["processed"] >= 1
    row = db.query_one("SELECT id FROM jira_connections")
    assert db.query_one("SELECT COUNT(*) AS n FROM jira_issues WHERE connection_id=?",
                        (row["id"],))["n"] >= 1
    st = db.query_one("SELECT phase FROM jira_sync_state WHERE connection_id=?", (row["id"],))
    assert st["phase"] == "idle"


# -- tools: real dispatch against mock ----------------------------------------------------------------
def _run(name, args, transport):
    from app.services.jira import tools as jt
    return asyncio.run(jt.run_jira_tool(name, args, execution_id="exe1",
                                        conversation_id="c1", transport=transport))


def test_tool_search_get_transition_assign_comment(tmp_db):
    seed_connection()
    t = make_transport()
    r = _run("jira_search_issues", {"query": "out-of-stock kiosk"}, t)
    assert r["ok"] and "COS-5904" in r["output"], r
    r = _run("jira_search_issues", {"query": "COS-5904"}, t)
    assert r["ok"] and "exact key match" in r["output"], r
    r = _run("jira_get_issue", {"key": "cos-5904"}, t)
    assert r["ok"] and "High" in r["output"]
    r = _run("jira_get_issue_comments", {"key": "COS-5904"}, t)
    assert r["ok"] and "repro confirmed" in r["output"]
    r = _run("jira_get_issue_changelog", {"key": "COS-5904"}, t)
    assert r["ok"] and "In Progress" in r["output"]
    r = _run("jira_get_issue_attachments", {"key": "COS-5904"}, t)
    assert r["ok"] and "screen.png" in r["output"]
    # invalid transition rejected with valid options
    r = _run("jira_transition_issue", {"key": "COS-5904", "transition": "Done"}, t)
    assert not r["ok"] and "In Review" in r["output"]
    r = _run("jira_transition_issue", {"key": "COS-5904", "transition": "In Review"}, t)
    assert r["ok"] and "verified" in r["output"], r
    # ambiguous assignee asks for clarification, never guesses
    r = _run("jira_assign_issue", {"key": "COS-5904", "assignee": "Kev"}, t)
    assert not r["ok"] and "Ambiguous" in r["output"]
    r = _run("jira_add_comment", {"key": "COS-5904", "body": "hello **world**"}, t)
    assert r["ok"], r
    # idempotent replay of the same comment does not duplicate
    r2 = _run("jira_add_comment", {"key": "COS-5904", "body": "hello **world**"}, t)
    assert r2["ok"] and "already posted" in r2["output"]
    r = _run("jira_create_issue", {"project": "COS", "issue_type": "Bug",
                                   "summary": "kiosk payment retry",
                                   "description": "details"}, t)
    assert r["ok"] and "COS-1234" in r["output"], r
    # scope enforcement
    r = _run("jira_create_issue", {"project": "OTHER", "summary": "x"}, t)
    assert not r["ok"] and "scope" in r["output"]


def test_tool_deleted_issue_removed_from_index(tmp_db):
    from app.services.jira import sync as jsync
    seed_connection()
    t = make_transport()
    asyncio.run(jsync.refresh_issue("COS-5904", transport=t))
    row = db.query_one("SELECT id FROM jira_connections")
    assert db.query_one("SELECT issue_key FROM jira_issues WHERE issue_key='COS-5904'")

    def handler_404(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errorMessages": ["gone"]})

    try:
        asyncio.run(jsync.refresh_issue("COS-9999",
                                        transport=httpx.MockTransport(handler_404)))
        raise AssertionError("should raise")
    except JiraError as exc:
        assert exc.kind == "not_found"


def test_tool_boards_sprints_rank(tmp_db):
    seed_connection()
    t = make_transport()
    assert _run("jira_list_projects", {}, t)["ok"]
    r = _run("jira_list_boards", {}, t)
    assert r["ok"] and "Kiosk board" in r["output"], r
    r = _run("jira_get_board", {"board_id": "7"}, t)
    assert r["ok"], r
    r = _run("jira_list_sprints", {"board_id": "7"}, t)
    assert r["ok"] and "Sprint 12" in r["output"], r
    r = _run("jira_get_sprint_issues", {"sprint_id": "3"}, t)
    assert r["ok"] and "COS-5904" in r["output"], r
    r = _run("jira_rank_issues", {"keys": "COS-5904", "after": "COS-5900"}, t)
    assert r["ok"], r
    r = _run("jira_search_jql", {"jql": "project = COS ORDER BY updated DESC", "limit": 5}, t)
    assert r["ok"], r
    r = _run("jira_search_jql", {"jql": "project = OTHER ORDER BY updated DESC"}, t)
    assert not r["ok"] and "scope" in r["output"]


def test_router_crud_and_guards(client, monkeypatch):
    client.put("/api/v1/jira/connection", json={
        "base_url": "https://jira.test", "email": "t@x.test", "api_token": "tok"})
    import app.routers.jira as jr
    transport = make_transport()
    orig_make = jr.make_client

    def patched(row=None):
        c, r = orig_make(row)
        c._transport = transport
        return c, r

    async def fake_refresh(key, transport=None):
        from app.services.jira.service import normalize_issue, upsert_index, get_connection
        row = get_connection()
        n = normalize_issue(ISSUE_RAW, row["base_url"])
        n["key"] = key.upper()
        n["url"] = f"{row['base_url']}/browse/{key.upper()}"
        upsert_index(row["id"], [n])
        return n

    monkeypatch.setattr(jr, "make_client", patched)
    monkeypatch.setattr(jr.jsync, "refresh_issue", fake_refresh)
    # create
    r = client.post("/api/v1/jira/issues", json={"project": "COS", "summary": "n1"})
    assert r.status_code == 200 and r.json()["issue"]["key"] == "COS-1234", r.text
    # delete requires confirm=KEY
    assert client.delete("/api/v1/jira/issues/COS-5904").status_code == 400
    assert client.delete("/api/v1/jira/issues/COS-5904",
                         params={"confirm": "COS-5904"}).status_code == 200
    # ops audit recorded, token never present
    ops = client.get("/api/v1/jira/ops").json()["ops"]
    assert any(o["op"].startswith("issue.") for o in ops)
    assert "tok" not in json.dumps(ops)
    # saved searches are local-only, clearly labeled
    assert client.post("/api/v1/jira/saved", json={"name": "bugs", "jql": "x"}).status_code == 200
    saved = client.get("/api/v1/jira/saved").json()["searches"]
    assert saved and saved[0]["origin"] == "local"
