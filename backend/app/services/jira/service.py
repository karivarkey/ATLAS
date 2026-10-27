"""High-level Jira service: connections, normalization, local index, audit.

Jira is the source of truth; SQLite holds a searchable operational cache.
Secrets never leave this module unencrypted; API tokens are never logged,
never returned to clients, and never injected into agent prompts.
"""
from __future__ import annotations

import json
import re
import time

from ... import db
from .adf import adf_to_markdown
from .client import JiraClient, JiraError, extract_key, normalize_base_url
from .crypto import decrypt_token, encrypt_token

SCOPE = "user:default"
DEFAULT_FIELDS = ["summary", "description", "issuetype", "status", "priority",
                  "project", "assignee", "reporter", "labels", "components",
                  "parent", "duedate", "attachment", "issuelinks", "sprint",
                  "customfield_10014", "customfield_10010", "updated", "created"]
SYNC_FIELDS = DEFAULT_FIELDS + ["comment"]
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
PREVIEW_MIMES = ("image/", "application/pdf", "text/")


def _redact(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: ("[redacted]" if k.lower() in ("authorization", "api_token",
                "token", "password") else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, str) and len(obj) > 24 and "JENC1." in obj:
        return "[redacted]"
    return obj


def audit_op(connection_id: str, op: str, issue_key: str = "", *,
             before: dict | None = None, after: dict | None = None,
             status: str = "ok", error: str = "", execution_id: str = "",
             conversation_id: str = "", actor: str = "user",
             idempotency_key: str = "") -> str:
    oid = db.new_id("jop")
    try:
        db.execute(
            "INSERT INTO jira_ops (id, connection_id, scope_id, op, issue_key,"
            " before_json, after_json, status, error, execution_id,"
            " conversation_id, actor, idempotency_key, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (oid, connection_id, SCOPE, op, issue_key,
             json.dumps(before or {}), json.dumps(after or {})[:8000],
             status, (error or "")[:1000], execution_id, conversation_id,
             actor, idempotency_key or oid, db.now_ms()))
    except Exception:
        pass
    db.audit(f"jira.{op}", {"issue": issue_key, "status": status,
                            "error": (error or "")[:200]}, actor=actor)
    return oid


def notify(kind: str, title: str, body: str, dedupe_key: str) -> None:
    try:
        from .. import tasks as _tasks
        _tasks.notify(kind, title, body[:500], dedupe_key=dedupe_key)
    except Exception:
        try:
            db.execute("INSERT OR IGNORE INTO notifications (id, kind, title, body,"
                       " dedupe_key, status, created_at) VALUES (?, ?, ?, ?, ?, 'unread', ?)",
                       (db.new_id("ntf"), kind, title[:200], body[:500],
                        dedupe_key, db.now_ms()))
        except Exception:
            pass


# -- connections ---------------------------------------------------------------
def get_connection() -> dict | None:
    row = db.query_one("SELECT * FROM jira_connections WHERE scope_id = ?", (SCOPE,))
    return dict(row) if row else None


def public_connection(row: dict | None) -> dict | None:
    """Connection status safe for browser clients: never includes the token."""
    if not row:
        return None
    projects = json.loads(row.get("project_keys") or "[]")
    indexed = db.query_one("SELECT COUNT(*) AS n FROM jira_issues WHERE connection_id = ?",
                           (row["id"],))
    pending = db.query_one("SELECT processed FROM jira_sync_state WHERE connection_id = ?",
                           (row["id"],))
    state = db.query_one("SELECT phase, error FROM jira_sync_state WHERE connection_id = ?",
                         (row["id"],))
    return {
        "id": row["id"], "display_name": row.get("display_name", ""),
        "base_url": row.get("base_url", ""), "email": row.get("email", ""),
        "project_keys": projects,
        "sync_prefs": json.loads(row.get("sync_prefs") or "{}"),
        "ai_write": bool(row.get("ai_write", 1)),
        "require_approval_deletes": bool(row.get("require_approval_deletes", 1)),
        "notify_prefs": json.loads(row.get("notify_prefs") or "{}"),
        "status": row.get("status", "disconnected"),
        "account_id": row.get("account_id", ""),
        "account_name": row.get("account_name", ""),
        "last_sync_at": row.get("last_sync_at", 0),
        "last_sync_error": row.get("last_sync_error", ""),
        "last_validated_at": row.get("last_validated_at", 0),
        "has_token": bool(row.get("token_enc")),
        "indexed_issues": (indexed["n"] if indexed else 0),
        "sync_phase": (state["phase"] if state else "idle"),
        "sync_detail": (state["error"] if state else ""),
        "sync_processed": (pending["processed"] if pending else 0),
        "project_count": len(projects),
    }


def save_connection(*, display_name: str, base_url: str, email: str,
                    api_token: str | None, project_keys: list[str] | None = None,
                    sync_prefs: dict | None = None, ai_write: bool = True,
                    require_approval_deletes: bool = True,
                    notify_prefs: dict | None = None) -> dict:
    base_url = normalize_base_url(base_url)
    email = (email or "").strip()
    if "@" not in email:
        raise JiraError("validation", "A valid Atlassian account email is required.")
    existing = get_connection()
    token_enc = (existing or {}).get("token_enc", "") if existing else ""
    if api_token:
        token_enc = encrypt_token(api_token)
    if not token_enc:
        raise JiraError("validation", "API token is required for a new connection.")
    now = db.now_ms()
    cid = (existing or {}).get("id") or db.new_id("jcon")
    db.execute(
        "INSERT INTO jira_connections (id, scope_id, display_name, base_url, email,"
        " token_enc, project_keys, sync_prefs, ai_write, require_approval_deletes,"
        " notify_prefs, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'disconnected', ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,"
        " base_url=excluded.base_url, email=excluded.email, token_enc=excluded.token_enc,"
        " project_keys=excluded.project_keys, sync_prefs=excluded.sync_prefs,"
        " ai_write=excluded.ai_write,"
        " require_approval_deletes=excluded.require_approval_deletes,"
        " notify_prefs=excluded.notify_prefs, updated_at=excluded.updated_at",
        (cid, SCOPE, (display_name or "").strip()[:120], base_url, email, token_enc,
         json.dumps(sorted({k.strip().upper() for k in (project_keys or []) if k.strip()})),
         json.dumps(sync_prefs or {}), 1 if ai_write else 0,
         1 if require_approval_deletes else 0, json.dumps(notify_prefs or {}),
         (existing or {}).get("created_at", now) if existing else now, now))
    db.execute("INSERT OR IGNORE INTO jira_sync_state (connection_id, phase, updated_at)"
               " VALUES (?, 'idle', ?)", (cid, now))
    audit_op(cid, "connection.save", actor="user")
    row = db.query_one("SELECT * FROM jira_connections WHERE id = ?", (cid,))
    return public_connection(dict(row))  # type: ignore[arg-type]


def delete_connection() -> None:
    row = get_connection()
    if not row:
        return
    cid = row["id"]
    for table in ("jira_issues", "jira_projects", "jira_attachments", "jira_boards",
                  "jira_sprints", "jira_sync_state"):
        db.execute(f"DELETE FROM {table} WHERE connection_id = ?", (cid,))
    try:
        db.execute("DELETE FROM jira_issues_fts WHERE rowid IN"
                   " (SELECT rowid FROM jira_issues_fts)")
    except Exception:
        pass
    db.execute("DELETE FROM jira_connections WHERE id = ?", (cid,))
    audit_op(cid, "connection.delete", actor="user")


def make_client(row: dict | None = None,
                transport=None) -> tuple[JiraClient, dict]:
    row = row or get_connection()
    if not row or not row.get("token_enc"):
        raise JiraError("auth", "Jira is not connected. Add credentials in Settings > Jira.")
    try:
        token = decrypt_token(row["token_enc"])
    except ValueError as exc:
        raise JiraError("auth", f"Stored Jira credential is unreadable: {exc}")
    client = JiraClient(row["base_url"], row["email"], token, transport=transport)
    return client, row


def check_project_scope(project_key: str, row: dict) -> None:
    allowed = json.loads(row.get("project_keys") or "[]")
    if allowed and (project_key or "").upper() not in allowed:
        raise JiraError("permission",
                        f"Project {project_key} is outside the configured scope "
                        f"({', '.join(allowed)}).")


# -- issue normalization + index -----------------------------------------------
def _name(user: Any) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("displayName") or user.get("name") or "")


def normalize_issue(raw: dict, base_url: str) -> dict:
    fields = raw.get("fields") or {}
    project = fields.get("project") or {}
    pkey = str(project.get("key") or "")
    key = str(raw.get("key") or "")
    desc_adf = fields.get("description")
    desc_md = adf_to_markdown(desc_adf) if desc_adf else ""
    status = fields.get("status") or {}
    parent = fields.get("parent") or {}
    # Epic link: new parent field or legacy customfield_10014.
    epic = ""
    ptype = ""
    try:
        ptype = str((parent.get("fields") or {}).get("issuetype", {}).get("name", ""))
    except Exception:
        ptype = ""
    parent_key = str(parent.get("key") or "")
    if ptype.lower() == "epic":
        epic = parent_key
    elif fields.get("customfield_10014"):
        epic = str(fields.get("customfield_10014") or "")
    sprint = ""
    for cf in ("customfield_10020", "customfield_10010", "sprint"):
        val = fields.get(cf)
        if isinstance(val, list) and val:
            last = val[-1] if isinstance(val[-1], dict) else {}
            sprint = str(last.get("name") or last) if isinstance(last, dict) else str(last)
            break
        elif isinstance(val, str) and val:
            sprint = val
            break
    components = [c.get("name", "") for c in fields.get("components", []) or []
                  if isinstance(c, dict)]
    links = []
    for link in fields.get("issuelinks", []) or []:
        lt = (link.get("type") or {}).get("name", "")
        for side in ("inwardIssue", "outwardIssue"):
            other = link.get(side)
            if other:
                links.append({"id": str(link.get("id", "")), "type": lt,
                              "direction": side, "key": other.get("key", ""),
                              "summary": ((other.get("fields") or {}).get("summary", ""))})
    comments_count = 0
    try:
        comments_count = int(((fields.get("comment") or {}).get("total")) or 0)
    except Exception:
        comments_count = 0
    return {
        "key": key, "remote_id": str(raw.get("id") or ""),
        "project_key": pkey, "summary": str(fields.get("summary") or ""),
        "description_md": desc_md,
        "description_adf": json.dumps(desc_adf) if desc_adf else "{}",
        "issue_type": str((fields.get("issuetype") or {}).get("name") or ""),
        "status": str(status.get("name") or ""),
        "status_category": str((status.get("statusCategory") or {}).get("name") or ""),
        "priority": str((fields.get("priority") or {}).get("name") or ""),
        "assignee_id": str((fields.get("assignee") or {}).get("accountId") or ""),
        "assignee_name": _name(fields.get("assignee")),
        "reporter_name": _name(fields.get("reporter")),
        "labels": json.dumps(fields.get("labels") or []),
        "components": json.dumps(components),
        "sprint": sprint, "parent_key": parent_key, "epic_key": epic,
        "due_date": str(fields.get("duedate") or ""),
        "url": f"{base_url}/browse/{key}" if key else "",
        "updated_remote": str(fields.get("updated") or ""),
        "created_remote": str(fields.get("created") or ""),
        "comments_count": comments_count, "links": links,
        "raw": raw,
    }


def upsert_index(connection_id: str, items: list[dict]) -> int:
    now = db.now_ms()
    n = 0
    for it in items:
        db.execute(
            "INSERT INTO jira_issues (connection_id, issue_key, remote_id, project_key,"
            " summary, description_md, description_adf, issue_type, status, status_category,"
            " priority, assignee_id, assignee_name, reporter_name, labels, components,"
            " sprint, parent_key, epic_key, due_date, url, raw_json, updated_remote, indexed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(connection_id, issue_key) DO UPDATE SET"
            " remote_id=excluded.remote_id, project_key=excluded.project_key,"
            " summary=excluded.summary, description_md=excluded.description_md,"
            " description_adf=excluded.description_adf, issue_type=excluded.issue_type,"
            " status=excluded.status, status_category=excluded.status_category,"
            " priority=excluded.priority, assignee_id=excluded.assignee_id,"
            " assignee_name=excluded.assignee_name, reporter_name=excluded.reporter_name,"
            " labels=excluded.labels, components=excluded.components, sprint=excluded.sprint,"
            " parent_key=excluded.parent_key, epic_key=excluded.epic_key,"
            " due_date=excluded.due_date, url=excluded.url, raw_json=excluded.raw_json,"
            " updated_remote=excluded.updated_remote, indexed_at=excluded.indexed_at",
            (connection_id, it["key"], it["remote_id"], it["project_key"], it["summary"],
             it["description_md"][:20000], it["description_adf"][:60000], it["issue_type"],
             it["status"], it["status_category"], it["priority"], it["assignee_id"],
             it["assignee_name"], it["reporter_name"], it["labels"], it["components"],
             it["sprint"], it["parent_key"], it["epic_key"], it["due_date"], it["url"],
             json.dumps(it["raw"])[:200000], it["updated_remote"], now))
        try:
            db.execute("INSERT INTO jira_issues_fts (issue_key, summary, description_md,"
                       " project_key, status, assignee_name, labels) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (it["key"], it["summary"], it["description_md"][:8000],
                        it["project_key"], it["status"], it["assignee_name"],
                        " ".join(json.loads(it["labels"] or "[]"))))
        except Exception:
            pass
        n += 1
    return n


def remove_from_index(connection_id: str, keys: list[str]) -> None:
    for k in keys:
        db.execute("DELETE FROM jira_issues WHERE connection_id = ? AND issue_key = ?",
                   (connection_id, k))
        try:
            db.execute("DELETE FROM jira_issues_fts WHERE issue_key = ?", (k,))
        except Exception:
            pass


def card(n: dict) -> dict:
    """Compact structured ticket card for chat + list views."""
    return {
        "key": n.get("key", ""), "summary": n.get("summary", ""),
        "status": n.get("status", ""), "status_category": n.get("status_category", ""),
        "priority": n.get("priority", ""), "issue_type": n.get("issue_type", ""),
        "project": n.get("project_key", ""), "assignee": n.get("assignee_name", ""),
        "reporter": n.get("reporter_name", ""), "labels": json.loads(n.get("labels") or "[]"),
        "sprint": n.get("sprint", ""), "parent": n.get("parent_key", ""),
        "epic": n.get("epic_key", ""), "due": n.get("due_date", ""),
        "updated": n.get("updated_remote", ""), "url": n.get("url", ""),
        "excerpt": (n.get("description_md") or "")[:280],
    }


def cards_markdown(cards: list[dict], reason: dict[str, str] | None = None) -> str:
    lines = []
    for c in cards:
        why = f" — _{reason.get(c['key'], '')}_" if reason and reason.get(c["key"]) else ""
        lines.append(
            f"**[{c['key']}]({c['url']})** · {c['summary']}{why}\n"
            f"{c['status']} · {c['priority'] or 'no priority'} · {c['issue_type']} · "
            f"{c['project']}" + (f" · @{c['assignee']}" if c.get("assignee") else " · unassigned")
        )
    return "\n\n".join(lines)
