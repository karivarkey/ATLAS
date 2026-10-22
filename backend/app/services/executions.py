"""Execution manager: concurrency limits, lifecycle ops, crash recovery.

- One asyncio.Task per execution, tracked in-memory and recoverable from DB.
- Global semaphore bounds concurrent LLM loops; per-conversation lock
  serializes overlapping writes to the same workspace.
- On startup, executions stuck in non-terminal states are marked
  `interrupted` with a safe retry path (never silently resumed).
"""
from __future__ import annotations

import asyncio
import json
import logging

from .. import config, db, events
from . import agent

log = logging.getLogger("atlas.exec")

_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_EXECUTIONS)
_tasks: dict[str, asyncio.Task] = {}
_conv_locks: dict[str, asyncio.Lock] = {}
_manager_lock = asyncio.Lock()


def _conv_lock(conversation_id: str) -> asyncio.Lock:
    lock = _conv_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _conv_locks[conversation_id] = lock
    return lock


async def submit(conversation_id: str, user_text: str, model: str) -> dict:
    conv = db.query_one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
    if not conv:
        raise KeyError("conversation not found")
    model = model or conv["model"] or config.DEFAULT_MODEL
    db.execute("UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
               (model, db.now_ms(), conversation_id))
    msg_id = db.new_id("msg")
    db.execute(
        "INSERT INTO messages (id, conversation_id, role, content, model, parts_json,"
        " execution_id, status, created_at) VALUES (?, ?, 'user', ?, ?, '[]', '', 'done', ?)",
        (msg_id, conversation_id, user_text, model, db.now_ms()),
    )
    from . import memory as _mem
    try:
        _mem.ingest_message(conversation_id, msg_id, "user", user_text)
    except Exception:
        pass  # indexing must never break sending; reconciler covers gaps
    if not conv["title"] or conv["title"] == "New conversation":
        title = (user_text.strip().split("\n")[0])[:60] or "New conversation"
        db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))

    execution_id = db.new_id("exe")
    ws_id = conv["workspace_id"] or conversation_id
    db.execute(
        "INSERT INTO executions (id, conversation_id, status, model, workspace_id,"
        " input_text, current_step, max_steps, pending_json, error, started_at, updated_at, ended_at)"
        " VALUES (?, ?, 'queued', ?, ?, ?, 0, ?, '{}', '', ?, ?, 0)",
        (execution_id, conversation_id, model, ws_id, user_text,
         config.MAX_STEPS_PER_EXECUTION, db.now_ms(), db.now_ms()),
    )
    db.execute("UPDATE conversations SET workspace_id = ? WHERE id = ?",
               (ws_id, conversation_id))
    events.append(execution_id, "turn_queued",
                  {"conversation_id": conversation_id, "model": model})

    async with _manager_lock:
        _tasks[execution_id] = asyncio.create_task(_supervised_run(execution_id, conversation_id))
    return {"execution_id": execution_id, "message_id": msg_id}


async def _supervised_run(execution_id: str, conversation_id: str) -> None:
    # Fair scheduling: wait for a global slot without blocking other chats' API.
    try:
        await _semaphore.acquire()
    except asyncio.CancelledError:
        events.force_execution_status(execution_id, "cancelled", "cancelled while queued")
        return
    try:
        async with _conv_lock(conversation_id):
            await agent.run_execution(execution_id)
    except asyncio.CancelledError:
        events.force_execution_status(execution_id, "cancelled", "cancelled by user")
        await events.abroadcast(execution_id, "cancelled", {})
    except Exception as exc:  # never leave an execution stuck
        log.exception("execution %s crashed", execution_id)
        try:
            events.force_execution_status(execution_id, "failed", f"runtime error: {exc}"[:2000])
        except Exception:
            pass
    finally:
        _semaphore.release()
        async with _manager_lock:
            _tasks.pop(execution_id, None)


def get_snapshot(execution_id: str) -> dict | None:
    row = db.query_one("SELECT * FROM executions WHERE id = ?", (execution_id,))
    if not row:
        return None
    return {
        "id": row["id"], "conversation_id": row["conversation_id"],
        "status": row["status"], "model": row["model"],
        "workspace_id": row["workspace_id"], "current_step": row["current_step"],
        "max_steps": row["max_steps"],
        "pending": json.loads(row["pending_json"] or "{}"),
        "error": row["error"], "updated_at": row["updated_at"],
    }


def active_for_conversation(conversation_id: str) -> dict | None:
    row = db.query_one(
        "SELECT * FROM executions WHERE conversation_id = ? AND ended_at = 0"
        " ORDER BY updated_at DESC LIMIT 1",
        (conversation_id,),
    )
    if not row:
        return None
    return get_snapshot(row["id"])


async def stop(execution_id: str) -> bool:
    row = db.query_one("SELECT status FROM executions WHERE id = ?", (execution_id,))
    if not row or row["status"] in events.TERMINAL:
        return False
    await agent.set_control(execution_id, cancel=True)
    async with _manager_lock:
        task = _tasks.get(execution_id)
    if task and not task.done():
        task.cancel()
        return True
    events.force_execution_status(execution_id, "cancelled", "cancelled by user")
    return True


async def pause(execution_id: str) -> bool:
    row = db.query_one("SELECT status FROM executions WHERE id = ?", (execution_id,))
    if not row or row["status"] != "running":
        return False
    await agent.set_control(execution_id, pause=True)
    return True


async def resume(execution_id: str) -> bool:
    row = db.query_one("SELECT status FROM executions WHERE id = ?", (execution_id,))
    if not row or row["status"] not in ("paused", "interrupted"):
        return False
    await agent.set_control(execution_id, pause=False)
    if row["status"] == "interrupted":
        return await retry_execution(execution_id)
    return True


async def answer(execution_id: str, text: str) -> bool:
    row = db.query_one("SELECT status FROM executions WHERE id = ?", (execution_id,))
    if not row or row["status"] != "waiting_input":
        return False
    await agent.set_control(execution_id, answer=text)
    return True


async def resolve_approval(execution_id: str, approved: bool) -> bool:
    row = db.query_one("SELECT status FROM executions WHERE id = ?", (execution_id,))
    if not row or row["status"] != "waiting_approval":
        return False
    await agent.set_control(execution_id, approval=approved)
    return True


async def retry_execution(execution_id: str, model: str = "") -> bool:
    row = db.query_one("SELECT * FROM executions WHERE id = ?", (execution_id,))
    if not row:
        return False
    if row["status"] not in events.TERMINAL and row["status"] not in ("interrupted",):
        # Only allow retry of a live execution via stop+resubmit semantics.
        return False
    conv_id = row["conversation_id"]
    text = row["input_text"] or "Continue."
    new_model = model or row["model"]
    # New execution keeps full history; the old one stays as audit trail.
    await submit(conv_id, text, new_model)
    return True


def recover_on_startup() -> int:
    """Mark orphaned live executions as interrupted. Returns count."""
    rows = db.query("SELECT id FROM executions WHERE ended_at = 0")
    for r in rows:
        try:
            events.force_execution_status(r["id"], "interrupted",
                                          "server restarted during execution; retry to continue")
        except Exception:
            pass
    return len(rows)
