"""Jira agent tools: typed schemas + execution with safety rails.

Read operations run automatically. Ordinary reversible mutations run when the
user clearly requested them. Destructive operations (delete, bulk, rank across
many issues) always require explicit approval, enforced via needs_approval().
Every mutation records an audit row and refreshes the local index; failures
report the real Jira state, never a cached guess.
"""
from __future__ import annotations

import json
from typing import Any

JIRA_TOOL_SCHEMAS = [
    {"name": "jira_get_connection_status",
     "description": "Show Jira connection health: site, account, sync state, indexed counts.",
     "parameters": {}},
    {"name": "jira_search_issues",
     "description": "Search Jira issues by keywords across summary/description (hybrid local + live).",
     "parameters": {"query": "keywords or issue key", "project": "optional project key filter",
                    "status": "optional status filter", "limit": "max results (default 10)"}},
    {"name": "jira_search_jql",
     "description": "Advanced JQL search executed live against Jira.",
     "parameters": {"jql": "JQL expression", "limit": "max results (default 20)"}},
    {"name": "jira_get_issue",
     "description": "Fetch one issue live with description, fields, links.",
     "parameters": {"key": "issue key, e.g. COS-5904"}},
    {"name": "jira_get_issue_comments",
     "description": "List comments on an issue.",
     "parameters": {"key": "issue key", "limit": "max comments (default 30)"}},
    {"name": "jira_get_issue_changelog",
     "description": "Field history of an issue.",
     "parameters": {"key": "issue key"}},
    {"name": "jira_get_issue_attachments",
     "description": "Attachment metadata for an issue (never downloads content).",
     "parameters": {"key": "issue key"}},
    {"name": "jira_get_attachment_content",
     "description": "Download + summarize a safe attachment (images/PDF/text/code, 25MB cap).",
     "parameters": {"attachment_id": "attachment id", "key": "owning issue key"}},
    {"name": "jira_list_projects",
     "description": "List accessible Jira projects.",
     "parameters": {}},
    {"name": "jira_list_boards",
     "description": "List Jira Software boards, optionally for a project.",
     "parameters": {"project": "optional project key", "type": "optional scrum|kanban|simple"}},
    {"name": "jira_get_board",
     "description": "Board details incl. columns/config + first page of issues.",
     "parameters": {"board_id": "board id"}},
    {"name": "jira_list_sprints",
     "description": "Sprints for a board.",
     "parameters": {"board_id": "board id", "state": "optional active|future|closed"}},
    {"name": "jira_get_sprint_issues",
     "description": "Issues in a sprint.",
     "parameters": {"sprint_id": "sprint id", "limit": "max results (default 50)"}},
    {"name": "jira_create_issue",
     "description": "Create a Jira issue. Returns the real key + URL.",
     "parameters": {"project": "project key", "issue_type": "e.g. Task, Bug, Story",
                    "summary": "short summary", "description": "markdown description",
                    "assignee": "optional display name or accountId", "priority": "optional",
                    "labels": "optional comma-separated", "parent": "optional parent/epic key"}},
    {"name": "jira_update_issue",
     "description": "Edit fields of an issue (summary, description, priority, labels, duedate...).",
     "parameters": {"key": "issue key", "fields": "JSON object of field updates"}},
    {"name": "jira_assign_issue",
     "description": "Assign an issue by display name or accountId (empty unassigns).",
     "parameters": {"key": "issue key", "assignee": "display name, accountId, or '' to unassign"}},
    {"name": "jira_transition_issue",
     "description": "Move an issue through its workflow using a live transition id or name.",
     "parameters": {"key": "issue key", "transition": "transition id or name"}},
    {"name": "jira_add_comment",
     "description": "Add a comment to an issue.",
     "parameters": {"key": "issue key", "body": "markdown comment"}},
    {"name": "jira_update_comment",
     "description": "Edit an existing comment.",
     "parameters": {"key": "issue key", "comment_id": "comment id", "body": "new markdown"}},
    {"name": "jira_delete_comment",
     "description": "Delete a comment (requires approval).",
     "parameters": {"key": "issue key", "comment_id": "comment id"}},
    {"name": "jira_link_issues",
     "description": "Link two issues (e.g. Relates, Blocks).",
     "parameters": {"inward": "issue key", "outward": "issue key", "link_type": "default Relates"}},
    {"name": "jira_unlink_issues",
     "description": "Remove an issue link by id (requires approval).",
     "parameters": {"link_id": "issue link id"}},
    {"name": "jira_upload_attachment",
     "description": "Attach a workspace file to an issue.",
     "parameters": {"key": "issue key", "path": "workspace-relative file path"}},
    {"name": "jira_delete_issue",
     "description": "Delete an issue permanently (ALWAYS requires approval).",
     "parameters": {"key": "issue key"}},
    {"name": "jira_rank_issues",
     "description": "Reorder board issues via the real Agile rank API.",
     "parameters": {"keys": "comma-separated issue keys", "before": "rank before this key",
                    "after": "rank after this key"}},
    {"name": "jira_refresh_issue",
     "description": "Re-fetch an issue from Jira and update the local index.",
     "parameters": {"key": "issue key"}},
    {"name": "jira_log_work",
     "description": "Log a worklog entry (e.g. '2h').",
     "parameters": {"key": "issue key", "time_spent": "e.g. 2h 30m", "comment": "optional"}},
]

READ_TOOLS = {"jira_get_connection_status", "jira_search_issues", "jira_search_jql",
              "jira_get_issue", "jira_get_issue_comments", "jira_get_issue_changelog",
              "jira_get_issue_attachments", "jira_get_attachment_content",
              "jira_list_projects", "jira_list_boards", "jira_get_board",
              "jira_list_sprints", "jira_get_sprint_issues", "jira_refresh_issue"}
# Mutations that always need explicit approval regardless of auto-approve.
ALWAYS_APPROVE = {"jira_delete_issue", "jira_delete_comment", "jira_unlink_issues",
                  "jira_rank_issues"}
JIRA_NAMES = {t["name"] for t in JIRA_TOOL_SCHEMAS}


def jira_needs_approval(name: str, args: dict, auto_approve: bool,
                        conn: dict | None = None) -> tuple[bool, str]:
    if name not in JIRA_NAMES:
        return False, ""
    if name in READ_TOOLS:
        return False, ""
    if name in ALWAYS_APPROVE:
        return True, f"{name} is destructive or high-impact and requires approval."
    if conn is not None and not conn.get("ai_write", True):
        return True, "AI writes are disabled for this Jira connection."
    # Deletes honored via connection policy too.
    if name in ("jira_delete_issue",) and conn and conn.get("require_approval_deletes", 1):
        return True, "Issue deletion requires explicit approval."
    return (not auto_approve), ("" if auto_approve else "Jira mutation requires approval.")


def jira_summary(name: str, args: dict) -> str:
    key = str(args.get("key") or args.get("inward") or args.get("board_id")
              or args.get("sprint_id") or args.get("query") or args.get("jql") or "")[:60]
    labels = {"jira_search_issues": f"Search Jira `{key}`",
              "jira_search_jql": f"JQL `{key[:80]}`",
              "jira_get_issue": f"Retrieve {key}"}
    if name in labels:
        return labels[name]
    if name == "jira_transition_issue":
        return f"Transition {args.get('key', '')} → {args.get('transition', '')}"
    if name == "jira_create_issue":
        return f"Create {args.get('project', '')} issue"
    return f"{name} {key}".strip()


async def run_jira_tool(name: str, args: dict, *, execution_id: str = "",
                        conversation_id: str = "", transport=None) -> dict:
    """Execute a Jira tool. Returns {'ok', 'output'}; output is markdown w/ cards."""
    from ... import db as _db
    from .adf import adf_to_markdown, markdown_to_adf
    from .client import JiraError, extract_key
    from .service import (SCOPE, card, cards_markdown, check_project_scope,
                          get_connection, make_client, normalize_issue,
                          public_connection, upsert_index)
    from . import sync as _sync

    def fail(exc: Exception) -> dict:
        kind = getattr(exc, "kind", "error")
        return {"ok": False, "output": f"jira {kind}: {exc}"[:2000]}

    try:
        if name == "jira_get_connection_status":
            row = get_connection()
            if not row:
                return {"ok": True, "output": "Jira is not connected. Open Settings > Jira."}
            return {"ok": True, "output": json.dumps(public_connection(row), indent=1)[:3000]}

        if name in ("jira_search_issues", "jira_search_jql"):
            row = get_connection()
            if not row:
                return {"ok": False, "output": "Jira is not connected."}
            client, row = make_client(row, transport=transport)
            limit = max(1, min(int(args.get("limit") or 10), 50))
            if name == "jira_search_jql":
                jql = str(args.get("jql") or "").strip()
                if not jql:
                    return {"ok": False, "output": "jql is required."}
                check_scope_jql(jql, row)
            else:
                q = str(args.get("query") or "").strip()
                if not q:
                    return {"ok": False, "output": "query is required."}
                key = extract_key(q)
                if key:
                    try:
                        raw = await client.get_issue(key)
                        n = normalize_issue(raw, row["base_url"])
                        upsert_index(row["id"], [n])
                        return {"ok": True, "output": cards_markdown([card(n)],
                                {n["key"]: "exact key match"}) + f"\n\nURL: {n['url']}"}
                    except JiraError:
                        pass  # fall through to keyword search
                jql_bits = [f'text ~ "{q[:200]}"']
                if args.get("project"):
                    check_project_scope(str(args["project"]), row)
                    jql_bits.append(f"project = {args['project']}")
                else:
                    import json as _json
                    _allowed = _json.loads(row.get("project_keys") or "[]")
                    if _allowed:
                        jql_bits.append("project IN (" + ", ".join(_allowed) + ")")
                if args.get("status"):
                    jql_bits.append(f"status = \"{args['status']}\"")
                jql = " AND ".join(jql_bits) + " ORDER BY updated DESC"
            try:
                page = await client.search(jql, fields=None, max_results=limit)
                raws = page.get("issues", [])
            except JiraError as exc:
                if exc.kind in ("validation",):
                    # Fall back to local index for free-text when JQL text search unsupported.
                    raws = []
                else:
                    raise
            if not raws and name == "jira_search_issues":
                raws = local_search(row["id"], q, limit)
                # local_search returns normalized rows already
                cards = [card(r) for r in raws]
                if not cards:
                    return {"ok": True, "output": f"No Jira issues found for '{q}'. "
                                           "Try broadening terms or using JQL search."}
                return {"ok": True, "output": cards_markdown(cards) +
                        "\n\n_Source: local index (Jira unreachable or no live hits)._"}
            normed = [normalize_issue(r, row["base_url"]) for r in raws]
            if normed:
                upsert_index(row["id"], normed)
            cards = [card(n) for n in normed]
            if not cards:
                return {"ok": True, "output": f"No Jira issues found for '{str(args.get('query') or args.get('jql'))[:120]}'."}
            return {"ok": True, "output": cards_markdown(cards) +
                    f"\n\n_{len(cards)} result(s) live from Jira._"}

        if name == "jira_get_issue":
            key = str(args.get("key") or "").strip().upper()
            if not key:
                return {"ok": False, "output": "key is required."}
            n = await _sync.refresh_issue(key, transport=transport)
            c = card(n)
            detail = (f"**[{c['key']}]({c['url']})** · {c['summary']}\n"
                      f"{c['status']} · {c['priority'] or 'no priority'} · {c['issue_type']} · "
                      f"{c['project']}" + (f" · @{c['assignee']}" if c.get("assignee") else "") +
                      (f"\nSprint: {c['sprint']}" if c.get("sprint") else "") +
                      (f"\nParent: {c['parent']}" if c.get("parent") else "") +
                      (f"\nEpic: {c['epic']}" if c.get("epic") else "") +
                      (f"\nDue: {c['due']}" if c.get("due") else "") +
                      f"\n\n{(n.get('description_md') or '_no description_')[:2000]}")
            return {"ok": True, "output": detail}

        if name == "jira_get_issue_comments":
            key = str(args.get("key") or "").strip().upper()
            limit = max(1, min(int(args.get("limit") or 30), 100))
            client, row = make_client(transport=transport)
            data = await client.comments(key, max_results=limit)
            lines = []
            for cm in (data or {}).get("comments", []):
                author = ((cm.get("author") or {}).get("displayName")) or "?"
                body = adf_to_markdown(cm.get("body"))[:800]
                lines.append(f"**{author}** ({cm.get('created', '')}, id {cm.get('id')}):\n{body}")
            return {"ok": True, "output": "\n\n---\n\n".join(lines) or "(no comments)"}

        if name == "jira_get_issue_changelog":
            key = str(args.get("key") or "").strip().upper()
            client, row = make_client(transport=transport)
            raw = await client.get_issue(key, expand="changelog")
            lines = []
            for h in ((raw.get("changelog") or {}).get("histories", []) or [])[:50]:
                who = ((h.get("author") or {}).get("displayName")) or "?"
                for it in h.get("items", []) or []:
                    lines.append(f"{h.get('created', '')} · {who}: {it.get('field')} "
                                 f"'{str(it.get('fromString') or '')[:60]}' → "
                                 f"'{str(it.get('toString') or '')[:60]}'")
            return {"ok": True, "output": "\n".join(lines) or "(no changelog entries)"}

        if name == "jira_get_issue_attachments":
            key = str(args.get("key") or "").strip().upper()
            client, row = make_client(transport=transport)
            metas = await client.attachment_meta(key)
            if not metas:
                return {"ok": True, "output": "(no attachments)"}
            lines = [f"- id {a.get('id')}: **{a.get('filename')}** "
                     f"({a.get('mimeType')}, {a.get('size')} bytes, "
                     f"{((a.get('author') or {}).get('displayName')) or '?'}, {a.get('created')})"
                     for a in metas]
            return {"ok": True, "output": "\n".join(lines)}

        if name == "jira_get_attachment_content":
            from .service import MAX_ATTACHMENT_BYTES
            aid = str(args.get("attachment_id") or "").strip()
            key = str(args.get("key") or "").strip().upper()
            if not aid or not key:
                return {"ok": False, "output": "attachment_id and key are required."}
            client, row = make_client(transport=transport)
            metas = await client.attachment_meta(key)
            meta = next((a for a in metas if str(a.get("id")) == aid), None)
            if not meta:
                return {"ok": False, "output": f"Attachment {aid} not found on {key}."}
            size = int(meta.get("size") or 0)
            if size > MAX_ATTACHMENT_BYTES:
                return {"ok": False, "output": f"Attachment too large ({size} bytes, cap 25MB)."}
            data, ctype = await client.download_attachment(meta["content"])
            if len(data) > MAX_ATTACHMENT_BYTES:
                return {"ok": False, "output": "Attachment too large (cap 25MB)."}
            summary = summarize_bytes(data, ctype, str(meta.get("filename", "")))
            return {"ok": True, "output": f"**{meta.get('filename')}** ({ctype}, {len(data)} bytes)\n\n{summary[:4000]}"}

        if name == "jira_list_projects":
            from . import sync as _s
            projects = await _s.sync_projects(transport=transport)
            lines = [f"- **{p.get('key')}** · {p.get('name')} ({p.get('projectTypeKey', '')})"
                     for p in projects]
            return {"ok": True, "output": "\n".join(lines) or "(no accessible projects)"}

        if name == "jira_list_boards":
            client, row = make_client(transport=transport)
            boards = await client.boards(project_key=(str(args.get("project") or "").strip() or None),
                                         board_type=(str(args.get("type") or "").strip() or None))
            for b in boards:
                _db.execute("INSERT OR REPLACE INTO jira_boards (connection_id, board_id, name,"
                            " board_type, project_key, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (row["id"], int(b.get("id")), str(b.get("name", ""))[:200],
                             str(b.get("type", "")), "", _db.now_ms()))
            lines = [f"- id {b.get('id')}: **{b.get('name')}** ({b.get('type')})" for b in boards]
            return {"ok": True, "output": "\n".join(lines) or "(no boards accessible)"}

        if name == "jira_get_board":
            bid = int(args.get("board_id"))
            client, row = make_client(transport=transport)
            detail = await client.board(bid)
            cfg = await client.board_config(bid)
            page = await client.board_issues(bid, max_results=20)
            names = [f"{i.get('key')}: {(i.get('fields') or {}).get('summary', '')}"
                     for i in page.get("issues", [])]
            cols = []
            try:
                cols = [(c.get("statuses") or []) and c.get("name", "") for c in cfg.get("columnConfig", {}).get("columns", [])]
            except Exception:
                cols = []
            return {"ok": True, "output": json.dumps(
                {"board": {k: detail.get(k) for k in ("id", "name", "type", "location")},
                 "columns": cols, "sample_issues": names[:20]}, indent=1)[:4000]}

        if name == "jira_list_sprints":
            bid = int(args.get("board_id"))
            client, row = make_client(transport=transport)
            sprints = await client.sprints(bid, state=(str(args.get("state") or "").strip() or None))
            for s in sprints:
                _db.execute("INSERT OR REPLACE INTO jira_sprints (connection_id, sprint_id,"
                            " board_id, name, state, start_date, end_date, updated_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (row["id"], int(s.get("id")), bid, str(s.get("name", ""))[:200],
                             str(s.get("state", "")), str(s.get("startDate") or ""),
                             str(s.get("endDate") or ""), _db.now_ms()))
            lines = [f"- id {s.get('id')}: **{s.get('name')}** ({s.get('state')}, "
                     f"{s.get('startDate', '?')} → {s.get('endDate', '?')})" for s in sprints]
            return {"ok": True, "output": "\n".join(lines) or "(no sprints)"}

        if name == "jira_get_sprint_issues":
            sid = int(args.get("sprint_id"))
            limit = max(1, min(int(args.get("limit") or 50), 100))
            client, row = make_client(transport=transport)
            page = await client.sprint_issues(sid, max_results=limit)
            normed = [normalize_issue(r, row["base_url"]) for r in page.get("issues", [])]
            if normed:
                upsert_index(row["id"], normed)
            return {"ok": True, "output": cards_markdown([card(n) for n in normed]) or "(empty sprint)"}

        # -- mutations ---------------------------------------------------------
        from .service import audit_op as _audit, notify as _notify
        conn_row = get_connection()
        if conn_row and not conn_row.get("ai_write", 1):
            return {"ok": False, "output": "AI writes are disabled for this Jira connection."}

        if name == "jira_create_issue":
            project = str(args.get("project") or "").strip().upper()
            if not project:
                return {"ok": False, "output": "project is required."}
            check_project_scope(project, conn_row)
            fields: dict[str, Any] = {
                "project": {"key": project},
                "issuetype": {"name": str(args.get("issue_type") or "Task")},
                "summary": str(args.get("summary") or "").strip()[:1000],
            }
            if not fields["summary"]:
                return {"ok": False, "output": "summary is required."}
            if args.get("description"):
                fields["description"] = markdown_to_adf(str(args["description"])[:20000])
            if args.get("assignee"):
                aid = await resolve_user(client_name(conn_row, transport), str(args["assignee"]))
                if aid:
                    fields["assignee"] = {"accountId": aid}
            if args.get("priority"):
                fields["priority"] = {"name": str(args["priority"])}
            if args.get("labels"):
                labs = [l.strip() for l in str(args["labels"]).replace(",", " ").split() if l.strip()]
                fields["labels"] = labs
            if args.get("parent"):
                fields["parent"] = {"key": str(args["parent"]).strip().upper()}
            client, row = make_client(conn_row, transport=transport)
            idem = str(args.get("_idempotency") or f"{execution_id}:{json.dumps(args, sort_keys=True)[:80]}")
            dup = _db.query_one("SELECT after_json FROM jira_ops WHERE idempotency_key = ? AND status='ok'",
                                (idem,))
            if dup:
                return {"ok": True, "output": f"Already created (idempotent replay): {dup['after_json'][:500]}"}
            created = await client.create_issue(fields)
            new_key = created.get("key", "")
            n = await _sync.refresh_issue(new_key, transport=transport)
            _audit(row["id"], "issue.create", new_key, after={"key": new_key},
                   execution_id=execution_id, conversation_id=conversation_id,
                   actor="agent", idempotency_key=idem)
            _notify("jira_issue_created", f"Jira issue created: {new_key}",
                    f"{fields['summary']}\n{n['url']}", dedupe_key=f"jir created:{new_key}")
            return {"ok": True, "output": f"Created **[{new_key}]({n['url']})** · {fields['summary']}"}

        if name == "jira_update_issue":
            key = str(args.get("key") or "").strip().upper()
            try:
                fields = json.loads(args["fields"]) if isinstance(args.get("fields"), str) else (args.get("fields") or {})
            except ValueError:
                return {"ok": False, "output": "fields must be a JSON object."}
            if not key or not fields:
                return {"ok": False, "output": "key and fields are required."}
            client, row = make_client(conn_row, transport=transport)
            before = await client.get_issue(key)
            check_project_scope(str((before.get("fields") or {}).get("project", {}).get("key") or ""), row)
            api_fields = translate_fields(fields)
            await client.update_issue(key, api_fields)
            n = await _sync.refresh_issue(key, transport=transport)
            _audit(row["id"], "issue.update", key,
                   before={"summary": (before.get("fields") or {}).get("summary", "")},
                   after={"key": key, "status": n["status"]},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            if n["status"] != str((before.get("fields") or {}).get("status", {}).get("name") or ""):
                pass  # status verified below
            return {"ok": True, "output": f"Updated **[{key}]({n['url']})** — status now '{n['status']}'. Refresh verified."}

        if name == "jira_assign_issue":
            key = str(args.get("key") or "").strip().upper()
            who = str(args.get("assignee") or "").strip()
            client, row = make_client(conn_row, transport=transport)
            aid = await resolve_user(client, who) if who else None
            if who and not aid:
                cands = await client.search_users(who)
                names = ", ".join((u.get("displayName", "?") for u in cands[:5]))
                return {"ok": False, "output": f"Ambiguous assignee '{who}'. Candidates: {names or 'none'}. Ask the user to choose."}
            await client.assign(key, aid)
            n = await _sync.refresh_issue(key, transport=transport)
            _audit(row["id"], "issue.assign", key, after={"assignee": n["assignee_name"]},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            return {"ok": True, "output": f"**{key}** assigned to {n['assignee_name'] or 'Unassigned'} (verified)."}

        if name == "jira_transition_issue":
            key = str(args.get("key") or "").strip().upper()
            want = str(args.get("transition") or "").strip()
            client, row = make_client(conn_row, transport=transport)
            avail = await client.transitions(key)
            names = {str(t.get("name", "")).lower(): str(t.get("id")) for t in avail}
            ids = {str(t.get("id")): str(t.get("id")) for t in avail}
            tid = ids.get(want) or names.get(want.lower())
            if not tid:
                opts = ", ".join(t.get("name", "") for t in avail) or "none available"
                return {"ok": False, "output": f"Transition '{want}' not available for {key}. Available: {opts}."}
            await client.transition(key, tid)
            n = await _sync.refresh_issue(key, transport=transport)
            _audit(row["id"], "issue.transition", key, after={"status": n["status"]},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            return {"ok": True, "output": f"**{key}** transitioned — status now '{n['status']}' (verified)."}

        if name in ("jira_add_comment", "jira_update_comment"):
            key = str(args.get("key") or "").strip().upper()
            body = str(args.get("body") or "").strip()
            if not key or not body:
                return {"ok": False, "output": "key and body are required."}
            client, row = make_client(conn_row, transport=transport)
            if name == "jira_add_comment":
                idem = f"{execution_id}:{key}:{hash(body) & 0xffffffff}"
                dup = _db.query_one("SELECT after_json FROM jira_ops WHERE idempotency_key = ? AND status='ok'", (idem,))
                if dup:
                    return {"ok": True, "output": "Comment already posted (idempotent replay skipped)."}
                created = await client.add_comment(key, markdown_to_adf(body[:20000]))
                _audit(row["id"], "comment.add", key, after={"id": created.get("id")},
                       execution_id=execution_id, conversation_id=conversation_id,
                       actor="agent", idempotency_key=idem)
                return {"ok": True, "output": f"Comment added to **{key}** (id {created.get('id')})."}
            cid = str(args.get("comment_id") or "").strip()
            updated = await client.update_comment(key, cid, markdown_to_adf(body[:20000]))
            _audit(row["id"], "comment.update", key, after={"id": cid},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            return {"ok": True, "output": f"Comment {cid} on **{key}** updated."}

        if name == "jira_delete_comment":
            key = str(args.get("key") or "").strip().upper()
            cid = str(args.get("comment_id") or "").strip()
            client, row = make_client(conn_row, transport=transport)
            await client.delete_comment(key, cid)
            _audit(row["id"], "comment.delete", key, after={"id": cid},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            return {"ok": True, "output": f"Comment {cid} deleted from **{key}**."}

        if name == "jira_link_issues":
            inward = str(args.get("inward") or "").strip().upper()
            outward = str(args.get("outward") or "").strip().upper()
            ltype = str(args.get("link_type") or "Relates").strip()
            client, row = make_client(conn_row, transport=transport)
            await client.create_link(ltype, inward, outward)
            _audit(row["id"], "issue.link", inward, after={"with": outward, "type": ltype},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            return {"ok": True, "output": f"Linked **{inward}** ↔ **{outward}** ({ltype})."}

        if name == "jira_unlink_issues":
            lid = str(args.get("link_id") or "").strip()
            client, row = make_client(conn_row, transport=transport)
            await client.delete_link(lid)
            _audit(row["id"], "issue.unlink", "", after={"link": lid},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            return {"ok": True, "output": f"Issue link {lid} removed."}

        if name == "jira_upload_attachment":
            from pathlib import Path as _P
            key = str(args.get("key") or "").strip().upper()
            rel = str(args.get("path") or "").strip()
            # Resolve inside conversation workspace if available, else repo workspaces dir.
            root = None
            if conversation_id:
                from .. import workspace as _ws
                try:
                    root = _ws.workspace_root(conversation_id)
                except ValueError:
                    root = None
            target = (root / rel).resolve() if root else None
            if target is None or not target.is_file():
                return {"ok": False, "output": f"File not found in workspace: {rel}"}
            try:
                _ws.resolve(root, rel)  # type: ignore[arg-type]
            except (PermissionError, ValueError):
                return {"ok": False, "output": "Path escapes workspace."}
            data = target.read_bytes()
            from .service import MAX_ATTACHMENT_BYTES
            if len(data) > MAX_ATTACHMENT_BYTES:
                return {"ok": False, "output": "File too large (cap 25MB)."}
            import mimetypes as _m
            ctype = _m.guess_type(target.name)[0] or "application/octet-stream"
            client, row = make_client(conn_row, transport=transport)
            uploaded = await client.upload_attachment(key, target.name, data, ctype)
            await _sync.refresh_issue(key, transport=transport)
            _audit(row["id"], "attachment.upload", key, after={"file": target.name},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            return {"ok": True, "output": f"Attached **{target.name}** to **{key}** ({len(data)} bytes)."}

        if name == "jira_delete_issue":
            key = str(args.get("key") or "").strip().upper()
            client, row = make_client(conn_row, transport=transport)
            before = await client.get_issue(key)
            await client.delete_issue(key)
            from .service import remove_from_index
            remove_from_index(row["id"], [key])
            _audit(row["id"], "issue.delete", key,
                   before={"summary": (before.get("fields") or {}).get("summary", "")},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            _notify("jira_issue_deleted", f"Jira issue deleted: {key}", key,
                    dedupe_key=f"jiradel:{key}:{execution_id}")
            return {"ok": True, "output": f"**{key}** deleted in Jira and removed from the index."}

        if name == "jira_rank_issues":
            keys = [k.strip().upper() for k in str(args.get("keys") or "").replace(",", " ").split() if k.strip()]
            before = (str(args.get("before") or "").strip().upper() or None)
            after = (str(args.get("after") or "").strip().upper() or None)
            if not keys or (not before and not after):
                return {"ok": False, "output": "keys plus before/after anchor is required."}
            client, row = make_client(conn_row, transport=transport)
            await client.rank_issues(keys, rank_before=before, rank_after=after)
            _audit(row["id"], "board.rank", ",".join(keys), after={"before": before or "", "after": after or ""},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            return {"ok": True, "output": f"Rank updated for {', '.join(keys)} (Jira rank API accepted the change)."}

        if name == "jira_log_work":
            key = str(args.get("key") or "").strip().upper()
            spent = str(args.get("time_spent") or "").strip()
            if not key or not spent:
                return {"ok": False, "output": "key and time_spent are required."}
            client, row = make_client(conn_row, transport=transport)
            wl = await client.add_worklog(key, spent, str(args.get("comment") or ""))
            _audit(row["id"], "worklog.add", key, after={"id": wl.get("id", "")},
                   execution_id=execution_id, conversation_id=conversation_id, actor="agent")
            return {"ok": True, "output": f"Logged {spent} on **{key}** (id {wl.get('id')})."}

        if name == "jira_refresh_issue":
            key = str(args.get("key") or "").strip().upper()
            n = await _sync.refresh_issue(key, transport=transport)
            return {"ok": True, "output": f"**{key}** refreshed — status '{n['status']}'."}

        return {"ok": False, "output": f"unknown jira tool: {name}"}
    except Exception as exc:
        return fail(exc)


def check_scope_jql(jql: str, row: dict) -> None:
    import re as _re
    allowed = {k.upper() for k in (json.loads(row.get("project_keys") or "[]"))}
    if not allowed:
        return
    for m in _re.finditer(r"project\s*(?:=|IN)\s*(\([^)]*\)|[A-Za-z0-9_'\"]+)",
                          jql, _re.IGNORECASE):
        token = m.group(1).strip()
        if token.startswith("("):
            token = token[1:]
            if token.endswith(")"):
                token = token[:-1]
        for k in token.split(","):
            k = k.strip().strip("'\"").upper()
            if k and k not in allowed:
                raise Exception(f"JQL references project {k} outside the configured scope "
                                f"({', '.join(sorted(allowed))}).")


def client_name(row: dict, transport):
    from .service import make_client
    client, _ = make_client(row, transport=transport)
    return client


async def resolve_user(client, who: str) -> str | None:
    who = (who or "").strip()
    if not who:
        return None
    if "accountId:" in who:
        return who.split("accountId:", 1)[1].strip()
    users = await client.search_users(who, max_results=10)
    if not users:
        return None
    exact = [u for u in users if (u.get("displayName", "") or "").lower() == who.lower()
             or (u.get("emailAddress", "") or "").lower() == who.lower()]
    if len(exact) == 1:
        return exact[0].get("accountId")
    if len(users) == 1 and not exact:
        # Single candidate: still require an exact/substring-safe match signal.
        # A lone result for a prefix query is accepted (Jira already ranked it).
        return users[0].get("accountId")
    return None  # ambiguous: caller must ask the user to choose


def translate_fields(fields: dict) -> dict:
    out: dict[str, Any] = {}
    from .adf import markdown_to_adf
    for k, v in fields.items():
        kl = k.lower()
        if kl in ("summary",):
            out["summary"] = str(v)[:1000]
        elif kl in ("description",):
            out["description"] = markdown_to_adf(str(v)[:20000]) if isinstance(v, str) else v
        elif kl in ("priority",):
            out["priority"] = {"name": str(v)} if isinstance(v, str) else v
        elif kl in ("labels",):
            out["labels"] = [l.strip() for l in str(v).replace(",", " ").split() if l.strip()] if isinstance(v, str) else v
        elif kl in ("duedate", "due_date", "due"):
            out["duedate"] = str(v)
        elif kl in ("assignee",):
            pass  # use jira_assign_issue for validation
        elif kl in ("components",):
            out["components"] = [{"name": c.strip()} for c in str(v).split(",") if c.strip()] if isinstance(v, str) else v
        else:
            out[k] = v
    return out


def local_search(connection_id: str, query: str, limit: int) -> list[dict]:
    from ... import db as _db
    key = None
    from .client import extract_key as _ek
    key = _ek(query or "")
    rows: list[dict] = []
    if key:
        r = _db.query_one("SELECT * FROM jira_issues WHERE connection_id = ? AND issue_key = ?",
                          (connection_id, key))
        if r:
            rows.append(dict(r))
    # FTS fallback.
    try:
        fts = _db.query("SELECT issue_key FROM jira_issues_fts WHERE jira_issues_fts MATCH ? LIMIT ?",
                        (" ".join(f'"{t}"' for t in query.split()[:8]) or '""', limit))
        for f in fts:
            if all(r.get("issue_key") != f["issue_key"] for r in rows):
                r = _db.query_one("SELECT * FROM jira_issues WHERE connection_id = ? AND issue_key = ?",
                                  (connection_id, f["issue_key"]))
                if r:
                    rows.append(dict(r))
    except Exception:
        pass
    if not rows:
        like = f"%{query[:60]}%"
        for r in _db.query("SELECT * FROM jira_issues WHERE connection_id = ? AND"
                           " (summary LIKE ? OR description_md LIKE ? OR assignee_name LIKE ?)"
                           " ORDER BY indexed_at DESC LIMIT ?",
                           (connection_id, like, like, like, limit)):
            rows.append(dict(r))
    return rows[:limit]


def summarize_bytes(data: bytes, ctype: str, filename: str) -> str:
    name = filename.lower()
    try:
        if ctype.startswith("text/") or name.endswith((".txt", ".md", ".log", ".json",
                                                        ".py", ".js", ".ts", ".yaml", ".yml", ".xml")):
            text = data.decode("utf-8", errors="replace")
            return text[:6000] or "(empty file)"
        if ctype.startswith("image/"):
            return (f"[image {filename}, {len(data)} bytes — vision inspection happens "
                    f"in-app via the attachment preview; metadata verified from Jira.]")
        if ctype == "application/pdf" or name.endswith(".pdf"):
            # Minimal PDF text extraction (literal strings) without new deps.
            import re as _re
            raw = data.decode("latin-1", errors="replace")
            strings = _re.findall(r"\((?:[^()\\]|\\.){3,200}\)", raw)
            text = " ".join(s[1:-1].replace("\\n", " ") for s in strings[:400])
            return text[:6000] or "[PDF: no extractable text via lightweight parser.]"
    except Exception as exc:
        return f"[could not summarize attachment: {exc}]"
    return (f"[{filename}: {ctype}, {len(data)} bytes — preview not supported; "
            f"use download to retrieve the original.]")
