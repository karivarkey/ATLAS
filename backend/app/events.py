"""Durable per-execution event log + in-memory pub/sub for SSE streaming.

Every event is persisted with a monotonic per-execution sequence number
*before* being broadcast, so reconnecting clients can replay with
`?from_seq=N` without loss or duplication.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from . import db

VALID_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"starting", "cancelled"},
    "starting": {"running", "failed", "cancelled"},
    "running": {
        "waiting_input",
        "waiting_approval",
        "paused",
        "verifying",
        "failed",
        "cancelled",
        "timed_out",
        "interrupted",
    },
    "waiting_input": {"running", "cancelled", "failed"},
    "waiting_approval": {"running", "cancelled", "failed"},
    "paused": {"running", "cancelled", "failed"},
    "verifying": {"completed", "failed", "cancelled", "running"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
    "timed_out": set(),
    "interrupted": set(),
}

TERMINAL = {"completed", "failed", "cancelled", "timed_out", "interrupted"}


def valid_transition(frm: str, to: str) -> bool:
    if frm == to:
        return True
    return to in VALID_TRANSITIONS.get(frm, set())


_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_sub_lock = asyncio.Lock()


async def subscribe(execution_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    async with _sub_lock:
        _subscribers[execution_id].add(q)
    return q


async def unsubscribe(execution_id: str, q: asyncio.Queue) -> None:
    async with _sub_lock:
        _subscribers[execution_id].discard(q)


def _next_seq(execution_id: str) -> int:
    row = db.query_one(
        "SELECT COALESCE(MAX(seq), 0) AS m FROM events WHERE execution_id = ?",
        (execution_id,),
    )
    return int(row["m"]) + 1 if row else 1


def append(execution_id: str, type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist an event and broadcast it. Returns the full event dict."""
    seq = _next_seq(execution_id)
    event = {
        "execution_id": execution_id,
        "seq": seq,
        "type": type,
        "payload": payload or {},
        "created_at": db.now_ms(),
    }
    db.execute(
        "INSERT INTO events (execution_id, seq, type, payload_json, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (execution_id, seq, type, json.dumps(payload or {}), event["created_at"]),
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return event
    for q in list(_subscribers.get(execution_id, ())):
        try:
            loop.call_soon_threadsafe(q.put_nowait, event)
        except Exception:
            pass
    return event


async def abroadcast(execution_id: str, type: str, payload: dict[str, Any] | None = None):
    event = append(execution_id, type, payload)
    async with _sub_lock:
        queues = list(_subscribers.get(execution_id, ()))
    for q in queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event


def replay(execution_id: str, from_seq: int = 0, limit: int = 2000) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT seq, type, payload_json, created_at FROM events"
        " WHERE execution_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
        (execution_id, from_seq, limit),
    )
    return [
        {
            "execution_id": execution_id,
            "seq": r["seq"],
            "type": r["type"],
            "payload": json.loads(r["payload_json"] or "{}"),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def set_execution_status(execution_id: str, to: str, error: str = "") -> bool:
    row = db.query_one("SELECT status FROM executions WHERE id = ?", (execution_id,))
    if not row:
        return False
    frm = row["status"]
    if frm in TERMINAL:
        return False
    if not valid_transition(frm, to):
        return False
    db.execute(
        "UPDATE executions SET status = ?, error = CASE WHEN ? != '' THEN ? ELSE error END,"
        " updated_at = ?, ended_at = CASE WHEN ? IN"
        " ('completed','failed','cancelled','timed_out','interrupted') THEN ? ELSE ended_at END"
        " WHERE id = ?",
        (to, error, error, db.now_ms(), to, db.now_ms(), execution_id),
    )
    append(execution_id, "status_changed", {"from": frm, "to": to, "error": error})
    return True


def force_execution_status(execution_id: str, to: str, error: str = "") -> None:
    """Mark terminal state regardless of transition map (restart recovery)."""
    db.execute(
        "UPDATE executions SET status = ?, error = ?, updated_at = ?, ended_at = ? WHERE id = ?",
        (to, error, db.now_ms(), db.now_ms(), execution_id),
    )
    append(execution_id, "status_changed", {"from": "?", "to": to, "error": error})
