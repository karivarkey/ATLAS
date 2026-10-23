"""Chat integration helpers for notes + tasks (logic only — NOT wired into agent.py).

The owning agent wires these in with exactly 3 lines (see HOOKS.md):

    from . import noteagent as _na                                   # 1. import
    _ctx = _na.build_task_context(...)  # or _na.inject_chat_reminder(...)  # 2. call
    # 3. append _ctx text / payload to the agent prompt or tool registry
"""
from __future__ import annotations

from .. import db


def build_task_context(task_id: str) -> dict:
    """Assemble task + linked notes (current versions) + memory hits for an agent run."""
    from . import tasks as _tasks
    from . import vault as _vault

    t = _tasks.get_task(task_id)
    notes = []
    for link in t.get("notes", []):
        try:
            notes.append(_vault.get_note(link["path"]))
        except (KeyError, FileNotFoundError):
            notes.append({"id": link.get("note_id"), "path": link.get("path"),
                          "missing": True})
    memory = []
    try:
        from . import embeddings as _emb
        from . import memory as _mem
        import asyncio as _asyncio

        q = f"{t['title']} {t.get('description', '')}"[:1000]
        try:
            try:
                _asyncio.get_running_loop()
            except RuntimeError:
                # No running loop (tests / scripts): embed synchronously.
                vec = _asyncio.run(_emb.embed_batch([q]))[0]
            # Inside a running server loop: skip the blocking embed call —
            # lexical retrieval still works and execution must never block.
        except Exception:
            vec = None
        hits = _mem.hybrid_search(q, vec, _mem.DEFAULT_SCOPE, limit=5)
        memory = [{"chunk_id": h["chunk_id"], "score": h["score"],
                   "text": (h["text"] or "")[:600]} for h in hits]
    except Exception:
        memory = []
    return {"task": {k: v for k, v in t.items() if k != "notes"},
            "linked_notes": t.get("notes", []),
            "notes": notes, "memory": memory}


def inject_chat_reminder(conversation_id: str, limit: int = 5) -> dict:
    """Structured reminder payload for a conversation: overdue + due-today tasks."""
    from . import tasks as _tasks

    now = db.now_ms()
    day_start = now - (now % 86400000)
    cands = []
    for t in _tasks.list_tasks(limit=500):
        if t["status"] in ("completed", "cancelled", "archived"):
            continue
        due = int(t.get("due_at") or 0)
        overdue = bool(due and due < now)
        due_today = bool(due and now <= due < day_start + 86400000)
        if not (overdue or due_today):
            continue
        t["reminder_score"] = _tasks.reminder_score(t, now)
        t["overdue"] = overdue
        cands.append(t)
    cands.sort(key=lambda x: (-x["reminder_score"],
                              int(x.get("due_at") or 0)))
    return {"conversation_id": conversation_id, "reminders": cands[:limit],
            "count": len(cands)}


def _parse_ms(v) -> int:
    """Accept epoch ms (int/float/numeric string) or ISO date string → epoch ms, else 0."""
    if v is None or v == '':
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    try:
        # numeric string with decimals
        return int(float(s))
    except Exception:
        pass
    try:
        from datetime import datetime
        # ISO-8601 / 'YYYY-MM-DD' / 'YYYY-MM-DDTHH:MM'
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


async def run_notetask_tool(name: str, args: dict, scope: str,
                            execution_id: str = "") -> dict:
    """Structured vault + task tools for the agent loop.

    - note_read: reads a note AND records (execution_id, note version/hash)
      in note_reads so later writes can prove freshness.
    - note_create: create a new .md note (fails if it already exists).
    - note_write: requires expected_version + expected_hash from a prior
      note_read in the SAME execution; rejects stale writes with a conflict
      naming the current version (caller must re-read).
    - note_search / note_list: find notes before reading.
    - task_create: create a structured task (shown in the Tasks tab).
      Use for ANY "remind me / todo / task" request — never write
      reminder-*.txt files with fs_write.
    - task_list / task_get: find and inspect tasks.
    - task_update: safe field edits (title/description/priority/due/project).
    - task_transition: move status along the state machine.
    - reminder_create: attach a fire-at reminder to a task.
    """
    from . import tasks as _tasks
    from . import vault as _vault

    args = args or {}
    if name == "note_read":
        try:
            d = _vault.get_note(str(args.get("path", "")), record_exec=execution_id)
        except KeyError:
            return {"ok": False, "output": "note not found"}
        except FileNotFoundError as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True,
                "output": f"[{d['path']} v{d['version']}]\n{(d['content'] or '')[:8000]}",
                "version": d["version"], "hash": d["content_hash"]}
    if name == "note_create":
        path = str(args.get("path", "")).strip()
        content = str(args.get("content", "") or "")
        if not path:
            return {"ok": False, "output": "path is required (e.g. 'notes/mary-lamb.md')"}
        if not path.lower().endswith(".md"):
            path = f"{path}.md"
        try:
            d = _vault.create_note(path, content or f"# {path.split('/')[-1].removesuffix('.md')}\n\n",
                                   actor=f"execution:{execution_id}" if execution_id else "agent")
        except (ValueError, KeyError) as exc:
            return {"ok": False, "output": f"{exc} — try note_read + note_write to update it instead"}
        return {"ok": True, "output": f"created note {d['path']} v{d['version']}",
                "version": d["version"]}
    if name == "note_search":
        q = str(args.get("query", "") or "").strip()
        if not q:
            return {"ok": False, "output": "query is required"}
        try:
            hits = _vault.search_notes(q, limit=int(args.get("limit", 8) or 8))
        except Exception as exc:
            return {"ok": False, "output": str(exc)}
        if not hits:
            return {"ok": True, "output": "(no matching notes)"}
        lines = [f"- {h.get('path')} — {h.get('title', '')}" for h in hits[:10]]
        return {"ok": True, "output": "\n".join(lines)}
    if name == "note_list":
        try:
            tree = _vault.list_tree()
        except Exception as exc:
            return {"ok": False, "output": str(exc)}
        paths: list[str] = []

        def walk(ns):
            for n in ns or []:
                if n.get("type") == "note":
                    paths.append(n.get("path", ""))
                elif n.get("children"):
                    walk(n["children"])
        walk(tree)
        prefix = str(args.get("prefix", "") or "").strip()
        if prefix:
            paths = [p for p in paths if p.startswith(prefix)]
        paths = paths[: int(args.get("limit", 50) or 50)]
        return {"ok": True, "output": "\n".join(paths) or "(vault is empty)"}
    if name == "note_write":
        if not execution_id:
            return {"ok": False, "output": "note_write requires an execution context"}
        if args.get("expected_version") is None or not args.get("expected_hash"):
            return {"ok": False,
                    "output": "stale-guard: pass expected_version + expected_hash"
                             " from your note_read; re-read the note first"}
        try:
            d = _vault.update_note(str(args.get("path", "")), str(args.get("content", "")),
                                   actor=f"execution:{execution_id}",
                                   expected_version=int(args["expected_version"]),
                                   expected_hash=str(args["expected_hash"]))
        except _vault.NoteConflict as exc:
            return {"ok": False,
                    "output": f"conflict: {exc} "
                             f"(current version {exc.current_version}) — re-read and retry",
                    "current_version": exc.current_version,
                    "current_hash": exc.current_hash}
        except (KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"wrote {d['path']} v{d['version']}",
                "version": d["version"]}
    if name == "task_get":
        try:
            t = _tasks.get_task(str(args.get("task_id", "")))
        except KeyError:
            return {"ok": False, "output": "task not found"}
        return {"ok": True, "output": str({k: v for k, v in t.items()
                                           if k in ("id", "title", "description", "status",
                                                    "priority", "due_at", "project")})[:4000]}
    if name == "task_create":
        title = str(args.get("title", "") or "").strip()
        if not title:
            return {"ok": False, "output": "title is required"}
        try:
            t = _tasks.create_task(
                title=title,
                description=str(args.get("description", "") or ""),
                priority=str(args.get("priority", "") or "normal").strip() or "normal",
                due_at=_parse_ms(args.get("due_at")),
                due_tz=str(args.get("due_tz", "") or ""),
                project=str(args.get("project", "") or ""),
                source_conv=str(args.get("source_conv", "") or scope or ""),
                actor=f"execution:{execution_id}" if execution_id else "agent",
            )
        except (_tasks.TaskError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        # Optional: attach a reminder in the same call.
        remind = _parse_ms(args.get("remind_at") or args.get("fire_at"))
        if remind:
            try:
                _tasks.create_reminder(t["id"], remind,
                                       actor=f"execution:{execution_id}" if execution_id else "agent")
            except Exception as exc:
                return {"ok": True,
                        "output": f"task {t['id']} created (“{t['title']}”) but reminder failed: {exc}"}
        return {"ok": True,
                "output": f"task {t['id']} created (“{t['title']}”) status={t['status']}",
                "task_id": t["id"]}
    if name == "task_list":
        try:
            items = _tasks.list_tasks(
                status=str(args.get("status", "") or ""),
                project=str(args.get("project", "") or ""),
                priority=str(args.get("priority", "") or ""),
                limit=int(args.get("limit", 20) or 20),
            )
        except (_tasks.TaskError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        if not items:
            return {"ok": True, "output": "(no tasks match)"}
        lines = [f"- {t['id'][:8]} [{t['status']}/{t['priority']}] {t['title']}"
                 + (f" due={t['due_at']}" if t.get("due_at") else "")
                 for t in items[:20]]
        return {"ok": True, "output": "\n".join(lines)}
    if name == "task_transition":
        tid = str(args.get("task_id", "") or "")
        to = str(args.get("to", "") or "").strip()
        if not tid or not to:
            return {"ok": False, "output": "task_id and to are required"}
        try:
            t = _tasks.transition_task(tid, to,
                                       actor=f"execution:{execution_id}" if execution_id else "agent")
        except (_tasks.TaskError, KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"task {t['id']} → {t['status']}"}
    if name == "reminder_create":
        tid = str(args.get("task_id", "") or "")
        fire_at = _parse_ms(args.get("fire_at"))
        if not tid:
            return {"ok": False, "output": "task_id is required"}
        if not fire_at:
            return {"ok": False, "output": "fire_at is required (epoch ms or ISO date)"}
        try:
            _tasks.create_reminder(tid, fire_at,
                                   actor=f"execution:{execution_id}" if execution_id else "agent")
        except (_tasks.TaskError, KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"reminder set for task {tid} at {fire_at}"}
    if name == "task_update":
        # coerce due_at so ISO strings work from the model
        if "due_at" in args:
            args = {**args, "due_at": _parse_ms(args.get("due_at"))}
        try:
            t = _tasks.update_task(str(args.get("task_id", "")), actor=f"execution:{execution_id}",
                                   **{k: v for k, v in args.items()
                                      if k in ("title", "description", "priority", "due_at",
                                               "project", "due_tz")})
        except (_tasks.TaskError, KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"task {t['id']} updated"}
    return {"ok": False, "output": f"unknown note/task tool: {name}"}


NOTETASK_TOOL_SCHEMAS = [
    {"name": "note_create",
     "description": "Create a NEW vault note. Use when the user asks to write/save a note. Fails if it exists (then read+write it).",
     "parameters": {"path": "vault-relative .md path, e.g. 'notes/mary-lamb.md'",
                    "content": "full markdown content"}},
    {"name": "note_read",
     "description": "Read a vault note (records version for stale-write guard).",
     "parameters": {"path": "vault-relative .md path"}},
    {"name": "note_write",
     "description": "Update an EXISTING vault note. Requires expected_version + expected_hash from note_read in this run.",
     "parameters": {"path": "vault-relative .md path", "content": "full content",
                    "expected_version": "version from note_read",
                    "expected_hash": "hash from note_read"}},
    {"name": "note_search",
     "description": "Search vault notes by keyword before reading.",
     "parameters": {"query": "search text", "limit": "max results"}},
    {"name": "note_list",
     "description": "List vault note paths (optional prefix filter).",
     "parameters": {"prefix": "path prefix filter", "limit": "max results"}},
    {"name": "task_create",
     "description": "Create a structured task (appears in the Tasks tab). USE THIS for ANY remind/todo/task request — NEVER write reminder-*.txt files with fs_write.",
     "parameters": {"title": "short task title (required)",
                    "description": "details",
                    "priority": "critical|high|normal|low",
                    "due_at": "epoch ms or ISO date, 0 = none",
                    "project": "project name",
                    "remind_at": "optional epoch ms or ISO date for a reminder"}},
    {"name": "task_list",
     "description": "List tasks (optional status/project/priority filter).",
     "parameters": {"status": "inbox|planned|in_progress|waiting_input|waiting_approval|completed",
                    "project": "project name", "priority": "priority", "limit": "max results"}},
    {"name": "task_get",
     "description": "Read a structured task by id.",
     "parameters": {"task_id": "task id"}},
    {"name": "task_update",
     "description": "Update safe task fields (title/description/priority/due/project).",
     "parameters": {"task_id": "task id", "title": "new title", "description": "text",
                    "priority": "critical|high|normal|low", "due_at": "epoch ms or ISO date",
                    "project": "project name"}},
    {"name": "task_transition",
     "description": "Move a task along inbox→planned→in_progress→(waiting|completed|cancelled)→archived.",
     "parameters": {"task_id": "task id", "to": "target status"}},
    {"name": "reminder_create",
     "description": "Attach a reminder to an existing task.",
     "parameters": {"task_id": "task id", "fire_at": "epoch ms or ISO date"}},
]
