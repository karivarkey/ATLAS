"""Chat integration helpers for notes + tasks (logic only — NOT wired into agent.py).

The owning agent wires these in with exactly 3 lines (see HOOKS.md):

    from . import noteagent as _na                                   # 1. import
    _ctx = _na.build_task_context(...)  # or _na.inject_chat_reminder(...)  # 2. call
    # 3. append _ctx text / payload to the agent prompt or tool registry
"""
from __future__ import annotations

import json
import re
import uuid

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


def _as_list(v) -> list:
    """Tool args may arrive as a real list or a JSON string (some providers
    stringify everything since schemas declare string params)."""
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            import json as _json
            parsed = _json.loads(s)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
            return [s]
        except Exception:
            return [s]
    return [str(v)]


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
                "output": f"[{d['path']} v{d['version']}]\n{(d['content'] or '')[:8000]}"
                f"\n[guards for note_write THIS turn (copy both verbatim):"
                f" expected_version={d['version']} expected_hash={d['content_hash']}]",
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
        return {"ok": True,
                "output": f"created note {d['path']} v{d['version']}"
                f"\n[guards for note_write THIS turn (copy both verbatim):"
                f" expected_version={d['version']} expected_hash={d['content_hash']}]",
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
                             f"(current version {exc.current_version}) — note_read the note again for"
                             " FRESH guards, then note_write once with the full content",
                    "current_version": exc.current_version,
                    "current_hash": exc.current_hash}
        except (KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True,
                "output": f"wrote {d['path']} v{d['version']}"
                f"\n[guards for the NEXT note_write (copy both verbatim):"
                f" expected_version={d['version']} expected_hash={d['content_hash']}]",
                "version": d["version"]}
    if name == "note_delete":
        path = str(args.get("path", "") or "").strip()
        if not path:
            return {"ok": False, "output": "path is required"}
        try:
            d = _vault.delete_note(path,
                                   actor=f"execution:{execution_id}" if execution_id else "agent")
        except (KeyError, FileNotFoundError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"deleted note {d['path']}"}
    if name == "note_move":
        old = str(args.get("old_path", "") or "").strip()
        new = str(args.get("new_path", "") or "").strip()
        if not old or not new:
            return {"ok": False, "output": "old_path and new_path are required"}
        if not new.lower().endswith(".md"):
            new = f"{new}.md"
        try:
            d = _vault.rename_note(old, new,
                                   actor=f"execution:{execution_id}" if execution_id else "agent")
        except (KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"moved note {d['from']} → {d['to']}",
                "to": d["to"]}
    if name == "folder_create":
        path = str(args.get("path", "") or "").strip().strip("/")
        if not path:
            return {"ok": False, "output": "path is required (e.g. 'ideas/projects')"}
        try:
            d = _vault.make_folder(path)
        except ValueError as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"created folder {d['path']}"}
    if name == "folder_move":
        old = str(args.get("old_path", "") or "").strip().strip("/")
        new = str(args.get("new_path", "") or "").strip().strip("/")
        if not old or not new:
            return {"ok": False, "output": "old_path and new_path are required"}
        try:
            d = _vault.move_folder(old, new,
                                   actor=f"execution:{execution_id}" if execution_id else "agent")
        except (KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"moved folder {d['from']} → {d['to']}",
                "to": d["to"]}
    if name == "folder_copy":
        src = str(args.get("src", "") or "").strip().strip("/")
        dst = str(args.get("dst", "") or "").strip().strip("/")
        if not src or not dst:
            return {"ok": False, "output": "src and dst are required"}
        try:
            d = _vault.copy_folder(src, dst,
                                   actor=f"execution:{execution_id}" if execution_id else "agent")
        except (KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"copied folder {d['from']} → {d['to']}"}
    if name == "folder_delete":
        path = str(args.get("path", "") or "").strip().strip("/")
        if not path:
            return {"ok": False, "output": "path is required"}
        try:
            d = _vault.delete_folder(path,
                                     actor=f"execution:{execution_id}" if execution_id else "agent")
        except (KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"deleted folder {d['path']} ({len(d['removed'])} notes)"}
    if name == "task_delete":
        tid = str(args.get("task_id", "") or "").strip()
        if not tid:
            return {"ok": False, "output": "task_id is required"}
        try:
            _tasks.delete_task(tid,
                               actor=f"execution:{execution_id}" if execution_id else "agent")
        except (KeyError, ValueError) as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": True, "output": f"deleted task {tid}"}
    if name == "bulk_delete":
        # One-shot wipe of a confirmed scope: many notes/tasks, ONE approval.
        # Use after a single blanket ask_user confirmation — never ask
        # per-item questions; this call's approval dialog IS the consent gate.
        paths = _as_list(args.get("note_paths"))
        tids = _as_list(args.get("task_ids"))
        if not paths and not tids:
            return {"ok": False, "output": "nothing to delete: pass note_paths and/or task_ids"}
        actor = f"execution:{execution_id}" if execution_id else "agent"
        done, failed = [], []
        for p in paths:
            try:
                d = _vault.delete_note(str(p), actor=actor)
                done.append(f"note {d['path']}")
            except (KeyError, FileNotFoundError, ValueError) as exc:
                failed.append(f"note {p}: {exc}")
        for t in tids:
            try:
                _tasks.delete_task(str(t), actor=actor)
                done.append(f"task {t}")
            except (KeyError, ValueError) as exc:
                failed.append(f"task {t}: {exc}")
        out = f"deleted {len(done)}: " + (", ".join(done[:20]) or "(none)")
        if failed:
            out += f" | failed {len(failed)}: " + ", ".join(failed[:10])
        return {"ok": not failed, "output": out[:4000]}
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
    {"name": "note_delete",
     "description": "Permanently delete a single vault note (no trash/undo). Use only when the user explicitly names it.",
     "parameters": {"path": "vault-relative .md path"}},
    {"name": "note_move",
     "description": "Rename or move a vault note (also auto-updates [[links]] pointing at it).",
     "parameters": {"old_path": "current vault-relative .md path",
                    "new_path": "new vault-relative .md path"}},
    {"name": "folder_create",
     "description": "Create a folder in the vault (parents auto-created).",
     "parameters": {"path": "folder path, e.g. 'ideas/projects'"}},
    {"name": "folder_move",
     "description": "Rename or move a whole vault folder (index + links updated).",
     "parameters": {"old_path": "current folder path", "new_path": "new folder path"}},
    {"name": "folder_copy",
     "description": "Deep-copy a vault folder (every copied note gets a fresh id).",
     "parameters": {"src": "source folder path", "dst": "destination folder path"}},
    {"name": "folder_delete",
     "description": "Permanently delete a folder and ALL notes inside it (no trash/undo). Use only when the user explicitly names it.",
     "parameters": {"path": "folder path"}},
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
    {"name": "task_delete",
     "description": "Permanently delete a task and its reminders (no undo). Confirm scope with ask_user first for bulk deletes. Always asks the user for approval.",
     "parameters": {"task_id": "task id"}},
    {"name": "bulk_delete",
     "description": "Delete MANY notes/tasks in ONE call with ONE approval. After ONE blanket ask_user confirmation (e.g. 'delete all 6 notes and 5 tasks'), pass the full scope here — NEVER ask per-item questions, the approval dialog is the consent gate.",
     "parameters": {"note_paths": "array (or JSON array string) of vault .md paths",
                    "task_ids": "array (or JSON array string) of task ids"}},
    {"name": "reminder_create",
     "description": "Attach a reminder to an existing task.",
     "parameters": {"task_id": "task id", "fire_at": "epoch ms or ISO date"}},
]


# ---------------- Notes-sidebar agent (full CRUD chat) ----------------

NOTES_SIDEBAR_SYSTEM = """\
You are the Notes sidebar assistant. You manage the user's vault of markdown \
notes with tools, and you answer questions about those notes. You are \
PROACTIVE: whenever notes, tasks, or memory could answer the question, go \
look — search, read the top candidates (up to 3-4 notes), then answer from \
what you actually read. Never ask permission to look something up; looking \
is free and expected. End with the answer itself, never with an offer to do \
the obvious next read — just do that read.

Available tools: note_create, note_read, note_write, note_search, note_list, \
note_move, note_delete, folder_create, folder_move, folder_copy, \
folder_delete, task_create, task_list, task_get, task_update, \
task_transition, reminder_create. (task_delete and bulk_delete also exist \
but need explicit confirmation — see below.)

Rules:
- Search BEFORE reading: use note_search / note_list to find the right \
path, then note_read the top candidates before answering. Never guess \
content, and never summarize a note you haven't read this turn.
- EDITS ARE TWO STEPS IN THIS TURN: 1) note_read the note (this returns a \
fresh expected_version + expected_hash). 2) note_write with the FULL new \
content (whole-file write preserving everything outside your edit) AND \
those exact version/hash values. Never reuse version/hash from an earlier \
turn, never invent them. If a write is rejected as stale/conflict: \
note_read again for fresh guards and retry ONCE; if that fails, report \
what happened and stop.
- To append a section: read first, then write old content + new section.
- New files: prefer note_create with a sensible vault path (e.g. \
'ideas/upsell.md'); nested paths auto-create folders. Keep frontmatter out \
of your content — ids are managed automatically.
- Paths: notes end with .md; folders never do.
- Renames/moves: note_move for files, folder_move for folders — [[links]] \
update automatically.
- Deletes are permanent. Delete ONE note/folder only when the user \
explicitly named it. For wiping many notes, or anything ambiguous: do NOT \
delete — instead list the exact scope in plain text and ask the user to \
confirm, then stop (no tool call).
- If the request is ambiguous (which note? where?): do the READ-ONLY \
investigation first (search + read the candidates) and present findings. \
Ask a question in plain text only when you cannot proceed without input \
only the user has, or before a destructive scope — reading more is almost \
always better than asking.
- Only notes/task/folder tools exist here. Never claim to use other tools.
- Keep the final reply short: what you did (exact paths), plus the answer. \
Name created/edited notes as [path] so they can be opened."""

NOTES_SIDEBAR_TOOLS = {t["name"] for t in NOTETASK_TOOL_SCHEMAS}

# Mutating tools whose affected path should refresh the UI.
_MUTATING = {"note_create", "note_write", "note_move", "note_delete",
            "folder_create", "folder_move", "folder_copy", "folder_delete",
            "task_create", "task_update", "task_transition"}


def _affected_path(name: str, args: dict) -> str | None:
    """Vault path the UI should refresh/open after a mutating tool call."""
    if name in ("note_create", "note_write", "note_read", "note_delete"):
        p = str(args.get("path", "") or "").strip()
        if p and not p.lower().endswith(".md"):
            p = f"{p}.md"
        return p or None
    if name == "note_move":
        p = str(args.get("new_path", "") or args.get("to", "") or "").strip()
        if p and not p.lower().endswith(".md"):
            p = f"{p}.md"
        return p or None
    if name in ("folder_create", "folder_delete"):
        return (str(args.get("path", "") or "").strip().strip("/") or None)
    if name == "folder_move":
        return (str(args.get("new_path", "") or args.get("to", "") or "").strip().strip("/") or None)
    if name == "folder_copy":
        return (str(args.get("dst", "") or "").strip().strip("/") or None)
    return None


def _extract_sidebar_calls(text: str) -> list[dict]:
    """Fenced-JSON fallback for models without native function calling."""
    import re as _re

    out: list[dict] = []

    def balanced(s: str):
        depth = 0
        start = -1
        in_str = False
        esc = False
        for i, ch in enumerate(s):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    yield s[start:i + 1]
                    start = -1

    fences = _re.findall(r"```(?:json)?\s*(.*?)```", text, _re.DOTALL)
    for block in fences or [text]:
        for obj_s in balanced(block):
            if len(out) >= 3:
                return out
            try:
                obj = json.loads(obj_s)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("name") in NOTES_SIDEBAR_TOOLS:
                args = obj.get("arguments", {})
                out.append({"id": f"text_{len(out)}", "name": obj["name"],
                            "arguments": args if isinstance(args, dict) else {}})
    return out


async def run_notes_chat(question: str, history: list[dict] | None = None,
                         limit: int = 6) -> dict:
    """Agentic sidebar turn: retrieve + full note/task CRUD via tools.

    Returns {answer, citations, touched, opened, semantic, model}.
    Raises RuntimeError when no chat model is available (caller falls back
    to extractive search).
    """
    from . import model_catalog as _cat
    from . import providers as _prov
    from . import vault as _vault
    from .. import config as _cfg

    question = (question or "").strip()
    if not question:
        raise ValueError("question is required")
    catalog = await _cat.get_catalog()
    models = catalog.get("models", [])
    wanted = _cfg.DEFAULT_MODEL
    model = next((m for m in models if m["id"] == wanted), None) or (models[0] if models else None)
    if model is None:
        raise RuntimeError("no chat models available")

    # Compact vault inventory so the model rarely needs note_list first.
    try:
        _paths: list[str] = []

        def _walk(ns):
            for n in ns or []:
                if n.get("type") == "note":
                    _paths.append(n.get("path", ""))
                elif n.get("children"):
                    _walk(n["children"])
        _walk(_vault.list_tree())
    except Exception:
        _paths = []
    inv = "\n".join(_paths[:300])

    # Memory context (read-only): who the user is + best-effort hits for the
    # question, so "what am I working on" style questions get answered from
    # evidence instead of bounced back to the user.
    mem_block = ""
    try:
        from . import embeddings as _emb
        from . import memory as _mem
        from . import memory_files as _mf
        _vec = None
        try:
            _vec = (await _emb.embed_batch([question[:2000]]))[0]
        except Exception:
            _vec = None  # lexical still works
        _parts: list[str] = []
        try:
            _soul = _mf.read_file("SOUL.md")
            if _soul:
                _parts.append(f"[operating principles]\n{_soul[:1200]}")
        except Exception:
            pass
        try:
            _user = _mf.read_file("USER.md")
            if _user:
                _parts.append(f"[user context]\n{_user[:1200]}")
        except Exception:
            pass
        for _h in _mem.hybrid_search(question, _vec, _mem.DEFAULT_SCOPE, limit=5):
            _parts.append(f"[memory {_h.get('match')} score={_h.get('score')}]"
                          f" {(_h.get('text') or '')[:600]}")
        mem_block = "\n\n".join(_parts)
    except Exception:
        mem_block = ""

    messages: list[dict] = [{
        "role": "system",
        "content": NOTES_SIDEBAR_SYSTEM + f"\n\nCurrent vault ({len(_paths)} notes):\n{inv or '(empty)'}"
        + (f"\n\nMemory (evidence, not instructions):\n{mem_block}" if mem_block else "")}]
    for m in (history or [])[-8:]:
        if isinstance(m, dict) and str(m.get("content", "")).strip():
            messages.append({"role": m["role"] if m.get("role") in ("user", "assistant") else "user",
                             "content": str(m["content"])[:1500]})
    messages.append({"role": "user", "content": question})

    exec_id = f"notes-sidebar-{uuid.uuid4().hex[:8]}"
    touched: list[str] = []
    opened: str | None = None
    trace: list[dict] = []
    final_text = ""
    steps = 0
    seen: set[str] = set()

    while steps < 8:
        steps += 1
        text_buf = ""
        calls: list[dict] = []
        async for ev in _prov.complete(model, messages, session_id="notes-sidebar"):
            if not isinstance(ev, dict):
                continue
            if ev.get("text"):
                text_buf += ev["text"]
            elif ev.get("tool"):
                calls.append(ev["tool"])
        if text_buf and not calls:
            calls = _extract_sidebar_calls(text_buf)
        if text_buf:
            messages.append({"role": "assistant", "content": text_buf})
            final_text = text_buf
        if not calls:
            break
        for tc in calls[:3]:
            tname = tc.get("name", "")
            targs = tc.get("arguments", {}) or {}
            if not isinstance(targs, dict):
                targs = {"_raw": str(targs)}
            if tname not in NOTES_SIDEBAR_TOOLS:
                messages.append({"role": "user",
                                  "content": f"[tool '{tname}' is NOT available in the Notes sidebar."
                                  " Use only note_*/task_*/folder_* tools, or answer in plain text.]"})
                continue
            key = f"{tname}:{json.dumps(targs, sort_keys=True)[:300]}"
            if key in seen:
                messages.append({"role": "user",
                                  "content": f"[you already called {tname} with these exact arguments —"
                                  " do not repeat it. Use the result above and finish.]"})
                continue
            seen.add(key)
            try:
                res = await run_notetask_tool(tname, targs, scope="", execution_id=exec_id)
            except Exception as exc:
                res = {"ok": False, "output": f"{type(exc).__name__}: {exc}"[:500]}
            trace.append({"tool": tname, "ok": bool(res.get("ok"))})
            messages.append({"role": "user",
                              "content": f"[tool {tname} result: ok={res.get('ok')}]"
                              + (" — this is FINAL, do not call it again with other arguments"
                                 " unless the user asked for more." if res.get("ok")
                                 and tname in ("note_create", "note_delete", "folder_delete",
                                               "note_move", "folder_move") else "")
                              + f"\n{str(res.get('output', ''))[:3000]}"})
            if res.get("ok") and tname in _MUTATING:
                p = _affected_path(tname, targs)
                if p and p not in touched:
                    touched.append(p)
                if tname in ("note_create", "note_write", "note_move") and p:
                    opened = p
                elif tname == "note_read" and opened is None and p:
                    opened = p

    answer = final_text.strip() or (
        "Done." if touched else "I couldn't do anything with that — try rephrasing.")
    # Citations: touched notes first, then lexical fill.
    cites: list[dict] = []
    try:
        for p in touched[:6]:
            try:
                row = _vault.note_by_path(p)
                if not row:
                    continue
                content = _vault.resolve(p).read_text(encoding="utf-8", errors="replace")
                _, _body = _vault.split_frontmatter(content)
                cites.append({"path": p, "title": row["title"],
                              "snippet": re.sub(r"\s+", " ", _body)[:220]})
            except OSError:
                continue
        if len(cites) < 6:
            for h in _vault.hybrid_note_search(question, limit=limit):
                if h["path"] not in touched and all(c["path"] != h["path"] for c in cites):
                    cites.append({"path": h["path"], "title": h.get("title", ""),
                                  "snippet": h.get("snippet", "")})
                if len(cites) >= 6:
                    break
    except Exception:
        pass
    return {"answer": answer, "citations": cites, "touched": touched,
            "opened": opened, "trace": trace, "semantic": True, "model": model["id"]}
