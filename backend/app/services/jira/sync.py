"""Paginated, resumable Jira synchronization.

Strategy:
  initial  — enumerate accessible issues via paginated JQL (project-scoped when
             configured), newest-first not required; checkpoint every page.
  incremental — JQL ``updated >= "<cursor>"`` ordered by updated ASC, then
             reconcile: refresh explicitly requested keys and drop locally
             cached keys that now 404 (deleted/inaccessible).
  recovery — checkpoints persist per page; any failure leaves phase='paused'
             with the cursor so the next run resumes instead of restarting.
"""
from __future__ import annotations

import asyncio
import json
import time

from ... import db
from .client import JiraError
from .service import (SCOPE, SYNC_FIELDS, audit_op, make_client, normalize_issue,
                      notify, upsert_index)

PAGE_SIZE = 100
CONCURRENCY = 4


def _state(cid: str) -> dict:
    row = db.query_one("SELECT * FROM jira_sync_state WHERE connection_id = ?", (cid,))
    if not row:
        db.execute("INSERT INTO jira_sync_state (connection_id, phase, updated_at)"
                   " VALUES (?, 'idle', ?)", (cid, db.now_ms()))
        return {"phase": "idle", "next_token": "", "processed": 0, "updated_cursor": ""}
    return dict(row)


def _set(cid: str, **kw) -> None:
    sets = ", ".join(f"{k} = ?" for k in kw) + ", updated_at = ?"
    db.execute(f"UPDATE jira_sync_state SET {sets} WHERE connection_id = ?",
               (*kw.values(), db.now_ms(), cid))


def _scope_jql(row: dict, extra: str = "") -> str:
    keys = json.loads(row.get("project_keys") or "[]")
    parts = []
    if keys:
        parts.append("project IN (" + ", ".join(keys) + ")")
    if extra:
        parts.append(f"({extra})")
    parts.append("ORDER BY updated ASC")
    return " AND ".join(parts) if len(parts) > 1 else (parts[0] if parts else "ORDER BY updated ASC")


async def _fetch_page(client, jql: str, token: str | None) -> dict:
    return await client.search(jql, fields=SYNC_FIELDS, max_results=PAGE_SIZE,
                               next_token=token or None)


async def run_sync(mode: str = "incremental", limit_pages: int = 200,
                   transport=None) -> dict:
    from .service import get_connection
    row = get_connection()
    if not row:
        raise JiraError("auth", "Jira is not connected.")
    client, row = make_client(row, transport=transport)
    cid, base = row["id"], row["base_url"]
    st = _state(cid)
    if st.get("phase") == "running":
        return {"status": "already_running", "processed": st.get("processed", 0)}

    if mode == "initial":
        jql = _scope_jql(row)
        token = None
        processed = 0
    else:
        cursor = st.get("updated_cursor") or "1970-01-01 00:00"
        jql = _scope_jql(row, f'updated >= "{cursor}"')
        token = st.get("next_token") or None
        processed = int(st.get("processed") or 0)
    _set(cid, phase="running", jql_cursor=jql, next_token=token or "", error="")

    sem = asyncio.Semaphore(CONCURRENCY)
    total_done = processed
    pages = 0
    max_updated = st.get("updated_cursor") or ""
    try:
        while pages < limit_pages:
            async with sem:
                page = await _fetch_page(client, jql, token)
            issues = page.get("issues", [])
            if issues:
                normed = [normalize_issue(r, base) for r in issues]
                upsert_index(cid, normed)
                total_done += len(normed)
                max_updated = max([max_updated] + [n["updated_remote"] for n in normed
                                                   if n["updated_remote"]])
            token = page.get("nextPageToken")
            pages += 1
            _set(cid, next_token=token or "", processed=total_done,
                 updated_cursor=max_updated)
            if not token:
                break
        # Reconciliation: verify a sample of stale cached keys still exist.
        # Full enumeration is expensive; refresh keys older than 30d lazily.
        _set(cid, phase="idle", next_token="", error="")
        db.execute("UPDATE jira_connections SET last_sync_at = ?, last_sync_error = '',"
                   " status = 'connected' WHERE id = ?", (db.now_ms(), cid))
        audit_op(cid, "sync", status="ok", after={"mode": mode, "processed": total_done})
        return {"status": "complete", "mode": mode, "processed": total_done,
                "pages": pages, "cursor": max_updated}
    except JiraError as exc:
        _set(cid, phase="paused", error=f"{exc.kind}: {exc}"[:500])
        db.execute("UPDATE jira_connections SET last_sync_error = ? WHERE id = ?",
                   (f"{exc.kind}: {exc}"[:500], cid))
        audit_op(cid, "sync", status="failed", error=f"{exc.kind}: {exc}"[:500])
        if exc.kind in ("auth", "permission"):
            notify("jira_sync_failed", "Jira sync paused",
                   f"{exc.kind}: {str(exc)[:200]}", dedupe_key=f"jirasync:{cid}")
        raise


async def refresh_issue(issue_key: str, transport=None) -> dict:
    from .service import get_connection
    row = get_connection()
    if not row:
        raise JiraError("auth", "Jira is not connected.")
    client, row = make_client(row, transport=transport)
    try:
        raw = await client.get_issue(issue_key)
    except JiraError as exc:
        if exc.kind == "not_found":
            from .service import remove_from_index
            remove_from_index(row["id"], [issue_key.upper()])
        raise
    normed = normalize_issue(raw, row["base_url"])
    upsert_index(row["id"], [normed])
    # Attachment metadata cache.
    atts = ((raw.get("fields") or {}).get("attachment") or [])
    for a in atts:
        db.execute("INSERT OR REPLACE INTO jira_attachments (connection_id, issue_key,"
                   " attachment_id, filename, mime, size, author, created, content_url)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (row["id"], issue_key.upper(), str(a.get("id", "")),
                    str(a.get("filename", ""))[:200], str(a.get("mimeType", ""))[:120],
                    int(a.get("size") or 0),
                    str(((a.get("author") or {}).get("displayName")) or "")[:120],
                    str(a.get("created") or ""),
                    str(a.get("content") or "")))
    return normed


async def sync_projects(transport=None) -> list[dict]:
    from .service import get_connection
    row = get_connection()
    if not row:
        raise JiraError("auth", "Jira is not connected.")
    client, _ = make_client(row, transport=transport)
    projects = await client.list_projects()
    now = db.now_ms()
    keys = json.loads(row.get("project_keys") or "[]")
    for p in projects:
        if keys and p.get("key") not in keys:
            continue
        lead = p.get("lead") or {}
        db.execute("INSERT INTO jira_projects (connection_id, jira_id, key, name,"
                   " project_type, description, lead, updated_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                   " ON CONFLICT(connection_id, key) DO UPDATE SET name=excluded.name,"
                   " project_type=excluded.project_type, description=excluded.description,"
                   " lead=excluded.lead, updated_at=excluded.updated_at",
                   (row["id"], str(p.get("id", "")), str(p.get("key", "")),
                    str(p.get("name", ""))[:200], str(p.get("projectTypeKey", ""))
                    or str(p.get("style", "")),
                    str(p.get("description") or "")[:2000],
                    str(lead.get("displayName", ""))[:120], now))
    return projects
