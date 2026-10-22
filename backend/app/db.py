"""SQLite persistence layer (stdlib sqlite3, WAL mode).

Schema covers: conversations, messages, executions, events, artifacts,
model cache, settings KV, audit log. Migrations are additive and
idempotent; `init_db()` is safe to call on every startup.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New conversation',
    model TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,             -- user | assistant | tool | system
    content TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    parts_json TEXT NOT NULL DEFAULT '[]',
    execution_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done',  -- streaming | done | error | cancelled
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    model TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    input_text TEXT NOT NULL DEFAULT '',
    current_step INTEGER NOT NULL DEFAULT 0,
    max_steps INTEGER NOT NULL DEFAULT 60,
    pending_json TEXT NOT NULL DEFAULT '{}',  -- pending question / approval payload
    error TEXT NOT NULL DEFAULT '',
    started_at INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    ended_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_exec_conv ON executions(conversation_id, updated_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    UNIQUE (execution_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_exec ON events(execution_id, seq);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'file',  -- file | diff | log | screenshot
    summary TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS model_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload_json TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
"""


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            config.ensure_dirs()
            _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def reset_for_tests(tmp_path: Path) -> sqlite3.Connection:
    """Point the DB at a temp file (used by tests)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        config.DB_PATH = tmp_path / "test.db"  # type: ignore[assignment]
        return connect()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = connect().execute(sql, params)
        connect().commit()
        return cur


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return connect().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    with _lock:
        return connect().execute(sql, params).fetchone()


def audit(action: str, detail: dict[str, Any] | None = None, actor: str = "system") -> None:
    execute(
        "INSERT INTO audit (created_at, actor, action, detail_json) VALUES (?, ?, ?, ?)",
        (now_ms(), actor, action, json.dumps(detail or {})),
    )
