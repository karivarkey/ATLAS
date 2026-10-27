"""Jira REST surface: connection settings, search, issues, boards, sync."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .. import db
from ..services.jira.adf import adf_to_markdown, markdown_to_adf
from ..services.jira.client import JiraError
from ..services.jira.service import (audit_op, card, check_project_scope, get_connection,
                                     make_client, normalize_issue, notify, public_connection,
                                     save_connection, upsert_index)
from ..services.jira import sync as jsync
from ..services.jira import tools as jtools

router = APIRouter(prefix="/api/v1/jira", tags=["jira"])


def _err(exc: Exception) -> HTTPException:
    kind = getattr(exc, "kind", "server")
    status = getattr(exc, "status", 0) or 500
    mapping = {"auth": 401, "permission": 403, "not_found": 404,
               "validation": 400, "conflict": 409, "rate_limited": 429,
               "network": 502, "server": 500}
    return HTTPException(mapping.get(kind, 500),
                         f"jira_{kind}: {str(exc)[:600]}")


# -- connection ---------------------------------------------------------------
@router.get("/connection")
def api_get_connection():
    return {"connection": public_connection(get_connection())}


@router.put("/connection")
def api_put_connection(body: dict):
    try:
        return {"connection": save_connection(
            display_name=str(body.get("display_name") or ""),
            base_url=str(body.get("base_url") or ""),
            email=str(body.get("email") or ""),
            api_token=body.get("api_token"),
            project_keys=body.get("project_keys"),
            sync_prefs=body.get("sync_prefs"),
            ai_write=bool(body.get("ai_write", True)),
            require_approval_deletes=bool(body.get("require_approval_deletes", True)),
            notify_prefs=body.get("notify_prefs"))}
    except JiraError as exc:
        raise _err(exc)


@router.delete("/connection")
def api_delete_connection():
    from ..services.jira.service import delete_connection
    delete_connection()
    return {"ok": True}


@router.post("/connection/test")
async def api_test_connection():
    row = get_connection()
    if not row:
        raise HTTPException(400, "No Jira connection configured.")
    try:
        client, row = make_client(row)
        result = await client.validate()
        me = result["me"]
        perms = result["permissions"]
        db.execute("UPDATE jira_connections SET status='connected', account_id=?,"
                   " account_name=?, last_validated_at=? WHERE id=?",
                   (str(me.get("accountId", "")), str(me.get("displayName", "")),
                    db.now_ms(), row["id"]))
        audit_op(row["id"], "connection.test", status="ok", actor="user")
        return {"ok": True, "me": {"accountId": me.get("accountId"),
                                   "displayName": me.get("displayName"),
                                   "email": me.get("emailAddress")},
                "permissions": {k: bool(v.get("havePermission")) for k, v in perms.items()}}
    except JiraError as exc:
        try:
            db.execute("UPDATE jira_connections SET status='error', last_sync_error=? WHERE id=?",
                       (f"{exc.kind}: {exc}"[:500], row["id"]))
        except Exception:
            pass
        raise _err(exc)


@router.post("/sync")
async def api_sync(body: dict | None = None):
    mode = ((body or {}).get("mode") or "incremental")
    if mode not in ("initial", "incremental"):
        raise HTTPException(400, "mode must be initial|incremental")
    try:
        return await jsync.run_sync(mode=mode)
    except JiraError as exc:
        raise _err(exc)


# -- issues: local index search -------------------------------------------------
@router.get("/issues")
def api_search_local(q: str = "", project: str = "", status: str = "",
                     assignee: str = "", issue_type: str = "",
                     sprint: str = "", label: str = "",
                     sort: str = "updated", limit: int = 25, offset: int = 0):
    row = get_connection()
    if not row:
        return {"issues": [], "total": 0, "connected": False}
    where = ["connection_id = ?"]
    params: list[Any] = [row["id"]]
    if q:
        from ..services.jira.client import extract_key
        k = extract_key(q)
        if k:
            where.append("issue_key = ?")
            params.append(k)
        else:
            where.append("(summary LIKE ? OR description_md LIKE ? OR issue_key LIKE ?)")
            like = f"%{q[:80]}%"
            params += [like, like, like]
    for col, val in (("project_key", project), ("status", status),
                     ("assignee_name", assignee), ("issue_type", issue_type),
                     ("sprint", sprint)):
        if val:
            where.append(f"{col} LIKE ?")
            params.append(f"%{val[:60]}%")
    if label:
        where.append("labels LIKE ?")
        params.append(f"%{label[:60]}%")
    total = db.query_one(f"SELECT COUNT(*) AS n FROM jira_issues WHERE {' AND '.join(where)}",
                         tuple(params))["n"]
    order = "indexed_at DESC"
    rows = db.query(f"SELECT * FROM jira_issues WHERE {' AND '.join(where)}"
                    f" ORDER BY {order} LIMIT ? OFFSET ?", tuple(params + [limit, offset]))
    return {"issues": [card(dict(r)) for r in rows], "total": total,
            "connected": True, "synced_note": "Local index; Jira is source of truth."}


@router.get("/issues/{key}")
async def api_get_issue(key: str, refresh: bool = True):
    try:
        n = await jsync.refresh_issue(key.upper()) if refresh else None
    except JiraError as exc:
        if exc.kind == "not_found":
            raise _err(exc)
        # Fall back to cache on transient errors, labeled as stale.
        row = get_connection()
        r = db.query_one("SELECT * FROM jira_issues WHERE connection_id=? AND issue_key=?",
                         (row["id"], key.upper())) if row else None
        if not r:
            raise _err(exc)
        n = dict(r)
        n["_stale"] = True
        return {"issue": full_issue(n), "stale": True, "error": f"{exc.kind}: {exc}"[:300]}
    return {"issue": full_issue(n), "stale": False}


def full_issue(n: dict) -> dict:
    c = card(n)
    raw = {}
    try:
        raw = json.loads(n.get("raw_json") or "{}")
    except Exception:
        raw = {}
    fields = (raw.get("fields") or {}) if isinstance(raw, dict) else {}
    comments = []
    try:
        cc = (fields.get("comment") or {}).get("comments", [])
        for cm in cc[-20:]:
            comments.append({"id": str(cm.get("id", "")),
                             "author": ((cm.get("author") or {}).get("displayName")) or "?",
                             "created": cm.get("created", ""), "updated": cm.get("updated", ""),
                             "body_md": adf_to_markdown(cm.get("body"))[:4000]})
    except Exception:
        comments = []
    histories = []
    try:
        for h in ((raw.get("changelog") or {}).get("histories", []) or [])[:60]:
            histories.append({"id": str(h.get("id", "")), "created": h.get("created", ""),
                              "author": ((h.get("author") or {}).get("displayName")) or "?",
                              "items": [{"field": i.get("field"), "from": str(i.get("fromString") or "")[:120],
                                         "to": str(i.get("toString") or "")[:120]} for i in h.get("items", []) or []]})
    except Exception:
        histories = []
    atts = [dict(r) for r in db.query(
        "SELECT attachment_id AS id, filename, mime, size, author, created, content_url AS url"
        " FROM jira_attachments WHERE issue_key = ? ORDER BY created DESC LIMIT 100",
        (n.get("key", "").upper(),))]
    return {**c, "description_md": n.get("description_md", ""),
            "comments": comments, "changelog": histories,
            "attachments": atts, "links": n.get("links", [])}


@router.post("/issues")
async def api_create_issue(body: dict):
    row = get_connection()
    if not row:
        raise HTTPException(400, "Jira is not connected.")
    try:
        client, row = make_client(row)
        project = str(body.get("project") or "").strip().upper()
        check_project_scope(project, row)
        fields: dict = {"project": {"key": project},
                        "issuetype": {"name": str(body.get("issue_type") or "Task")},
                        "summary": str(body.get("summary") or "")[:1000]}
        if not body.get("summary"):
            raise HTTPException(400, "summary is required")
        if body.get("description"):
            fields["description"] = markdown_to_adf(str(body["description"])[:20000])
        for k in ("priority", "labels", "components", "parent", "assignee", "duedate"):
            if body.get(k) is not None:
                fields.update(jtools.translate_fields({k: body[k]}))
        created = await client.create_issue(fields)
        n = await jsync.refresh_issue(created.get("key", ""))
        audit_op(row["id"], "issue.create", n["key"], actor="user")
        notify("jira_issue_created", f"Jira issue created: {n['key']}",
               n["url"], dedupe_key=f"jir created:{n['key']}")
        return {"issue": full_issue(n)}
    except JiraError as exc:
        raise _err(exc)


@router.patch("/issues/{key}")
async def api_update_issue(key: str, body: dict):
    try:
        client, row = make_client()
        before = await client.get_issue(key.upper())
        check_project_scope(str((before.get("fields") or {}).get("project", {}).get("key") or ""), row)
        await client.update_issue(key.upper(), jtools.translate_fields(body.get("fields") or body))
        n = await jsync.refresh_issue(key.upper())
        audit_op(row["id"], "issue.update", key.upper(), actor="user")
        return {"issue": full_issue(n)}
    except JiraError as exc:
        raise _err(exc)


@router.delete("/issues/{key}")
async def api_delete_issue(key: str, confirm: str = ""):
    if confirm != key.upper():
        raise HTTPException(400, "Deletion requires confirm=<ISSUE-KEY>.")
    try:
        client, row = make_client()
        await client.delete_issue(key.upper())
        from ..services.jira.service import remove_from_index
        remove_from_index(row["id"], [key.upper()])
        audit_op(row["id"], "issue.delete", key.upper(), actor="user")
        return {"ok": True, "deleted": key.upper()}
    except JiraError as exc:
        raise _err(exc)


# -- workflow / assignment --------------------------------------------------------
@router.get("/issues/{key}/transitions")
async def api_transitions(key: str):
    try:
        client, _ = make_client()
        return {"transitions": await client.transitions(key.upper())}
    except JiraError as exc:
        raise _err(exc)


@router.post("/issues/{key}/transition")
async def api_transition(key: str, body: dict):
    try:
        client, row = make_client()
        tid = str(body.get("transition_id") or body.get("transition") or "")
        if not tid.startswith("t:"):
            avail = await client.transitions(key.upper())
            match = next((t for t in avail if str(t.get("id")) == tid
                          or str(t.get("name", "")).lower() == tid.lower()), None)
            if not match:
                raise JiraError("validation", "Transition not available: "
                                + ", ".join(t.get("name", "") for t in avail))
            tid = str(match["id"])
        else:
            tid = tid[2:]
        await client.transition(key.upper(), tid)
        n = await jsync.refresh_issue(key.upper())
        audit_op(row["id"], "issue.transition", key.upper(), actor="user",
                 after={"status": n["status"]})
        return {"issue": full_issue(n)}
    except JiraError as exc:
        raise _err(exc)


@router.post("/issues/{key}/assign")
async def api_assign(key: str, body: dict):
    try:
        client, row = make_client()
        who = str(body.get("assignee") or "").strip()
        aid = await jtools.resolve_user(client, who) if who else None
        if who and not aid:
            raise JiraError("validation", f"Assignee not resolved: {who}")
        await client.assign(key.upper(), aid)
        n = await jsync.refresh_issue(key.upper())
        audit_op(row["id"], "issue.assign", key.upper(), actor="user",
                 after={"assignee": n["assignee_name"]})
        return {"issue": full_issue(n)}
    except JiraError as exc:
        raise _err(exc)


# -- comments ----------------------------------------------------------------------
@router.get("/issues/{key}/comments")
async def api_comments(key: str, limit: int = 30):
    try:
        client, _ = make_client()
        data = await client.comments(key.upper(), max_results=min(limit, 100))
        out = [{"id": str(c.get("id", "")), "author": ((c.get("author") or {}).get("displayName")) or "?",
                "created": c.get("created", ""), "updated": c.get("updated", ""),
                "body_md": adf_to_markdown(c.get("body"))[:4000]} for c in (data or {}).get("comments", [])]
        return {"comments": out, "total": (data or {}).get("total", len(out))}
    except JiraError as exc:
        raise _err(exc)


@router.post("/issues/{key}/comments")
async def api_add_comment(key: str, body: dict):
    if not (body.get("body") or "").strip():
        raise HTTPException(400, "body is required")
    try:
        client, row = make_client()
        created = await client.add_comment(key.upper(), markdown_to_adf(body["body"][:20000]))
        audit_op(row["id"], "comment.add", key.upper(), actor="user")
        return {"comment": {"id": str(created.get("id", ""))}}
    except JiraError as exc:
        raise _err(exc)


@router.patch("/issues/{key}/comments/{cid}")
async def api_update_comment(key: str, cid: str, body: dict):
    try:
        client, row = make_client()
        await client.update_comment(key.upper(), cid, markdown_to_adf(str(body.get("body") or "")[:20000]))
        audit_op(row["id"], "comment.update", key.upper(), actor="user")
        return {"ok": True}
    except JiraError as exc:
        raise _err(exc)


@router.delete("/issues/{key}/comments/{cid}")
async def api_delete_comment(key: str, cid: str):
    try:
        client, row = make_client()
        await client.delete_comment(key.upper(), cid)
        audit_op(row["id"], "comment.delete", key.upper(), actor="user")
        return {"ok": True}
    except JiraError as exc:
        raise _err(exc)


# -- attachments ----------------------------------------------------------------------
@router.get("/issues/{key}/attachments")
async def api_attachments(key: str):
    try:
        client, row = make_client()
        metas = await client.attachment_meta(key.upper())
        for a in metas:
            db.execute("INSERT OR REPLACE INTO jira_attachments (connection_id, issue_key,"
                       " attachment_id, filename, mime, size, author, created, content_url)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (row["id"], key.upper(), str(a.get("id", "")),
                        str(a.get("filename", ""))[:200], str(a.get("mimeType", ""))[:120],
                        int(a.get("size") or 0),
                        str(((a.get("author") or {}).get("displayName")) or "")[:120],
                        str(a.get("created") or ""), str(a.get("content") or "")))
        return {"attachments": [
            {"id": str(a.get("id", "")), "filename": a.get("filename"), "mime": a.get("mimeType"),
             "size": a.get("size"), "author": ((a.get("author") or {}).get("displayName")) or "?",
             "created": a.get("created")} for a in metas]}
    except JiraError as exc:
        raise _err(exc)


@router.get("/attachments/{aid}/download")
async def api_attachment_download(aid: str, issue: str = ""):
    from ..services.jira.service import MAX_ATTACHMENT_BYTES
    row = get_connection()
    if not row:
        raise HTTPException(400, "Jira is not connected.")
    meta = db.query_one("SELECT * FROM jira_attachments WHERE connection_id=? AND attachment_id=?",
                        (row["id"], aid))
    url = (meta["content_url"] if meta else "") or ""
    if not url and not issue:
        raise HTTPException(404, "attachment not found; pass ?issue=KEY")
    try:
        client, _ = make_client(row)
        if not url:
            metas = await client.attachment_meta(issue.upper())
            m = next((a for a in metas if str(a.get("id")) == aid), None)
            if not m:
                raise HTTPException(404, "attachment not found")
            url = m["content"]
        data, ctype = await client.download_attachment(url)
    except JiraError as exc:
        raise _err(exc)
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "attachment too large")
    safe = (meta["filename"] if meta else "attachment").replace('"', "_")[:150]
    return Response(content=data, media_type=ctype.split(";")[0] or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"',
                             "X-Content-Type-Options": "nosniff"})


@router.get("/attachments/{aid}/preview")
async def api_attachment_preview(aid: str, issue: str = ""):
    """Inline-safe preview info: text excerpt for safe types, metadata otherwise."""
    from ..services.jira.service import MAX_ATTACHMENT_BYTES, PREVIEW_MIMES
    row = get_connection()
    if not row:
        raise HTTPException(400, "Jira is not connected.")
    meta = db.query_one("SELECT * FROM jira_attachments WHERE connection_id=? AND attachment_id=?",
                        (row["id"], aid))
    url = (meta["content_url"] if meta else "") or ""
    try:
        client, _ = make_client(row)
        if not url:
            if not issue:
                raise HTTPException(404, "attachment not found; pass ?issue=KEY")
            metas = await client.attachment_meta(issue.upper())
            m = next((a for a in metas if str(a.get("id")) == aid), None)
            if not m:
                raise HTTPException(404, "attachment not found")
            url = m["content"]
            meta = {"filename": m.get("filename", ""), "mime": m.get("mimeType", ""),
                    "size": m.get("size", 0)}
        data, ctype = await client.download_attachment(url)
    except JiraError as exc:
        raise _err(exc)
    mime = str((meta["mime"] if meta else "") or ctype or "")
    if len(data) > MAX_ATTACHMENT_BYTES:
        return {"preview": "too_large", "mime": mime, "size": len(data)}
    if not (mime.startswith(PREVIEW_MIMES) or mime == "application/octet-stream"):
        return {"preview": "unsupported", "mime": mime, "size": len(data),
                "download": f"/api/v1/jira/attachments/{aid}/download"}
    text = jtools.summarize_bytes(data, mime, str((meta['filename'] if meta else '') or ""))
    return {"preview": "text" if len(text) < 20000 else "excerpt", "mime": mime,
            "size": len(data), "text": text[:8000]}


# -- projects / boards / sprints --------------------------------------------------------
@router.get("/projects")
async def api_projects():
    try:
        projects = await jsync.sync_projects()
        rows = db.query("SELECT key, name, project_type, description, lead, issue_count"
                        " FROM jira_projects WHERE connection_id = ? ORDER BY key",
                        (get_connection()["id"],))
        counts = {r["key"]: dict(r) for r in rows}
        out = []
        for p in projects:
            c = counts.get(p.get("key"), {})
            out.append({"key": p.get("key"), "name": p.get("name"),
                        "type": p.get("projectTypeKey") or p.get("style", ""),
                        "description": (c.get("description") or "")[:500],
                        "lead": c.get("lead", "")})
        return {"projects": out}
    except JiraError as exc:
        raise _err(exc)


@router.get("/projects/{key}/issues")
def api_project_issues(key: str, status: str = "", limit: int = 25, offset: int = 0):
    return api_search_local(q="", project=key, status=status, limit=limit, offset=offset)


@router.get("/boards")
async def api_boards(project: str = "", type: str = ""):
    try:
        client, row = make_client()
        boards = await client.boards(project_key=project or None, board_type=type or None)
        return {"boards": [{"id": b.get("id"), "name": b.get("name"), "type": b.get("type"),
                             "project": ((b.get("location") or {}).get("projectKey")) or ""} for b in boards]}
    except JiraError as exc:
        raise _err(exc)


@router.get("/boards/{bid}")
async def api_board(bid: int):
    try:
        client, _ = make_client()
        return {"board": await client.board(bid),
                "config": await client.board_config(bid),
                "issues": (await client.board_issues(bid, max_results=25)).get("issues", [])}
    except JiraError as exc:
        raise _err(exc)


@router.get("/boards/{bid}/issues")
async def api_board_issues(bid: int, jql: str = "", limit: int = 25, start: int = 0):
    try:
        client, row = make_client()
        page = await client.board_issues(bid, jql=jql, max_results=min(limit, 100), start_at=start)
        normed = [normalize_issue(r, row["base_url"]) for r in page.get("issues", [])]
        return {"issues": [card(n) for n in normed], "total": page.get("total")}
    except JiraError as exc:
        raise _err(exc)


@router.get("/boards/{bid}/sprints")
async def api_sprints(bid: int, state: str = ""):
    try:
        client, _ = make_client()
        return {"sprints": await client.sprints(bid, state=state or None)}
    except JiraError as exc:
        raise _err(exc)


@router.get("/sprints/{sid}/issues")
async def api_sprint_issues(sid: int, limit: int = 50, start: int = 0):
    try:
        client, row = make_client()
        page = await client.sprint_issues(sid, max_results=min(limit, 100), start_at=start)
        normed = [normalize_issue(r, row["base_url"]) for r in page.get("issues", [])]
        return {"issues": [card(n) for n in normed], "total": page.get("total")}
    except JiraError as exc:
        raise _err(exc)


@router.post("/rank")
async def api_rank(body: dict):
    try:
        client, row = make_client()
        keys = body.get("keys") or []
        if isinstance(keys, str):
            keys = [k.strip() for k in keys.replace(",", " ").split() if k.strip()]
        await client.rank_issues([k.upper() for k in keys],
                                 rank_before=(body.get("before") or None),
                                 rank_after=(body.get("after") or None))
        audit_op(row["id"], "board.rank", ",".join(keys), actor="user")
        return {"ok": True}
    except JiraError as exc:
        raise _err(exc)


# -- JQL / filters / saved searches ----------------------------------------------------------
@router.post("/jql/validate")
async def api_jql_validate(body: dict):
    try:
        client, _ = make_client()
        return await client.validate_jql(str(body.get("jql") or ""))
    except JiraError as exc:
        raise _err(exc)


@router.post("/jql/search")
async def api_jql_search(body: dict):
    try:
        client, row = make_client()
        jql = str(body.get("jql") or "")
        jtools.check_scope_jql(jql, row)
        limit = max(1, min(int(body.get("limit") or 25), 100))
        page = await client.search(jql, fields=None, max_results=limit,
                                   next_token=body.get("cursor"))
        normed = [normalize_issue(r, row["base_url"]) for r in page.get("issues", [])]
        if normed:
            upsert_index(row["id"], normed)
        return {"issues": [card(n) for n in normed], "cursor": page.get("nextPageToken"),
                "total": page.get("total")}
    except JiraError as exc:
        raise _err(exc)
    except Exception as exc:
        raise HTTPException(400, str(exc)[:300])


@router.get("/filters")
async def api_filters():
    try:
        client, _ = make_client()
        return {"filters": await client.filters()}
    except JiraError as exc:
        raise _err(exc)


@router.get("/saved")
def api_saved_list():
    row = get_connection()
    if not row:
        return {"searches": []}
    rows = db.query("SELECT id, name, jql, origin, remote_id, updated_at FROM jira_saved_searches"
                    " WHERE connection_id=? ORDER BY updated_at DESC", (row["id"],))
    return {"searches": [dict(r) for r in rows]}


@router.post("/saved")
def api_saved_create(body: dict):
    row = get_connection()
    if not row:
        raise HTTPException(400, "Jira is not connected.")
    sid = db.new_id("jsv")
    now = db.now_ms()
    db.execute("INSERT INTO jira_saved_searches (id, connection_id, scope_id, name, jql,"
               " origin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'local', ?, ?)",
               (sid, row["id"], "user:default", str(body.get("name") or "")[:120],
                str(body.get("jql") or "")[:2000], now, now))
    return {"id": sid}


@router.delete("/saved/{sid}")
def api_saved_delete(sid: str):
    row = get_connection()
    db.execute("DELETE FROM jira_saved_searches WHERE id=? AND connection_id=?",
               (sid, row["id"] if row else ""))
    return {"ok": True}


# -- links / worklogs / ops -----------------------------------------------------------------------
@router.post("/issues/{key}/links")
async def api_link(key: str, body: dict):
    try:
        client, row = make_client()
        await client.create_link(str(body.get("link_type") or "Relates"), key.upper(),
                                 str(body.get("other") or "").upper())
        audit_op(row["id"], "issue.link", key.upper(), actor="user")
        return {"ok": True}
    except JiraError as exc:
        raise _err(exc)


@router.delete("/links/{lid}")
async def api_unlink(lid: str):
    try:
        client, row = make_client()
        await client.delete_link(lid)
        audit_op(row["id"], "issue.unlink", actor="user")
        return {"ok": True}
    except JiraError as exc:
        raise _err(exc)


@router.get("/issues/{key}/worklogs")
async def api_worklogs(key: str):
    try:
        client, _ = make_client()
        return {"worklogs": await client.worklogs(key.upper())}
    except JiraError as exc:
        raise _err(exc)


@router.post("/issues/{key}/worklogs")
async def api_add_worklog(key: str, body: dict):
    try:
        client, row = make_client()
        wl = await client.add_worklog(key.upper(), str(body.get("time_spent") or ""),
                                      str(body.get("comment") or ""))
        audit_op(row["id"], "worklog.add", key.upper(), actor="user")
        return {"worklog": wl}
    except JiraError as exc:
        raise _err(exc)


@router.get("/ops")
def api_ops(limit: int = 50):
    row = get_connection()
    if not row:
        return {"ops": []}
    rows = db.query("SELECT id, op, issue_key, status, error, actor, execution_id,"
                    " conversation_id, created_at FROM jira_ops WHERE connection_id=?"
                    " ORDER BY created_at DESC LIMIT ?", (row["id"], min(limit, 200)))
    return {"ops": [dict(r) for r in rows]}
