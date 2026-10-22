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


async def run_notetask_tool(name: str, args: dict, scope: str,
                            execution_id: str = "") -> dict:
    """Future tool dispatch for the agent loop (vault + tasks).

    - note_read: reads a note AND records (execution_id, note version/hash)
      in note_reads so later writes can prove freshness.
    - note_write: requires expected_version + expected_hash from a prior
      note_read in the SAME execution; rejects stale writes with a conflict
      naming the current version (caller must re-read).
    - task_get / task_update: scoped task reads and safe field updates.
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
    if name == "task_update":
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
    {"name": "note_read",
     "description": "Read a vault note (records version for stale-write guard).",
     "parameters": {"path": "vault-relative .md path"}},
    {"name": "note_write",
     "description": "Write a vault note. Requires expected_version + expected_hash from note_read.",
     "parameters": {"path": "vault-relative .md path", "content": "full content",
                    "expected_version": "version from note_read",
                    "expected_hash": "hash from note_read"}},
    {"name": "task_get",
     "description": "Read a structured task by id.",
     "parameters": {"task_id": "task id"}},
    {"name": "task_update",
     "description": "Update safe task fields (title/description/priority/due/project).",
     "parameters": {"task_id": "task id", "title": "new title", "description": "text",
                    "priority": "critical|high|normal|low", "due_at": "epoch ms",
                    "project": "project name"}},
]
