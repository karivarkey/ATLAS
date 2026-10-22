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

-- ============ Atlas Memory Engine (Layers A-D) ============
-- Layer B: searchable chunks (deterministic ids for idempotent reindex)
CREATE TABLE IF NOT EXISTS memory_chunks (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    execution_id TEXT NOT NULL DEFAULT '',
    record_id TEXT NOT NULL DEFAULT '',
    scope_id TEXT NOT NULL DEFAULT 'user:default',
    kind TEXT NOT NULL DEFAULT 'message',
    text TEXT NOT NULL,
    char_start INTEGER NOT NULL DEFAULT 0,
    embedding BLOB,
    embedding_model TEXT NOT NULL DEFAULT '',
    embedding_dim INTEGER NOT NULL DEFAULT 0,
    indexed_at INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_conv ON memory_chunks(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chunks_scope ON memory_chunks(scope_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chunks_msg ON memory_chunks(message_id);

-- FTS5 lexical index (external content, synced by triggers for immediacy)
CREATE VIRTUAL TABLE IF NOT EXISTS memory_chunks_fts USING fts5(
    text, content='memory_chunks', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS mem_chunks_ai AFTER INSERT ON memory_chunks BEGIN
    INSERT INTO memory_chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS mem_chunks_ad AFTER DELETE ON memory_chunks BEGIN
    INSERT INTO memory_chunks_fts(memory_chunks_fts, rowid, text)
    VALUES ('delete', old.rowid, old.text);
END;

-- Layer B reliability: transactional outbox for embedding work
CREATE TABLE IF NOT EXISTS memory_index_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try_at INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Layer C: structured memory records
CREATE TABLE IF NOT EXISTS memory_records (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'user:default',
    memory_type TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    predicate TEXT NOT NULL DEFAULT '',
    object_value TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'proposed',
    valid_from INTEGER NOT NULL DEFAULT 0,
    valid_until INTEGER NOT NULL DEFAULT 0,
    source_conversation_ids TEXT NOT NULL DEFAULT '[]',
    source_message_ids TEXT NOT NULL DEFAULT '[]',
    source_chunk_ids TEXT NOT NULL DEFAULT '[]',
    supersedes_memory_id TEXT NOT NULL DEFAULT '',
    embedding_model TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_scope ON memory_records(scope_id, status, updated_at);

CREATE TABLE IF NOT EXISTS memory_record_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

-- Layer D: versioned SOUL.md / USER.md projections
CREATE TABLE IF NOT EXISTS memory_file_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    version INTEGER NOT NULL,
    hash TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'system',
    created_at INTEGER NOT NULL,
    UNIQUE(path, version)
);

-- Dream engine jobs + proposals
CREATE TABLE IF NOT EXISTS dream_jobs (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued',
    trigger TEXT NOT NULL DEFAULT 'manual',
    watermark_from INTEGER NOT NULL DEFAULT 0,
    watermark_to INTEGER NOT NULL DEFAULT 0,
    conversation_ids TEXT NOT NULL DEFAULT '[]',
    model TEXT NOT NULL DEFAULT '',
    stats_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    ended_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS memory_proposals (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at INTEGER NOT NULL,
    decided_at INTEGER NOT NULL DEFAULT 0,
    decided_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS execution_memory_refs (
    execution_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (execution_id, chunk_id)
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
        # guarded migrations for pre-existing databases
        cols = [r["name"] for r in _conn.execute("PRAGMA table_info(conversations)")]
        if "scope_id" not in cols:
            _conn.execute("ALTER TABLE conversations ADD COLUMN scope_id TEXT NOT NULL DEFAULT 'user:default'")
        if "memory_excluded" not in cols:
            _conn.execute("ALTER TABLE conversations ADD COLUMN memory_excluded INTEGER NOT NULL DEFAULT 0")
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


def ensure_column(table: str, column: str, ddl: str) -> None:
    """Idempotent ADD COLUMN for migrations on pre-existing databases."""
    cols = [r["name"] for r in query(f"PRAGMA table_info({table})")]
    if column not in cols:
        execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def transaction():
    """Context manager for atomic multi-statement writes (outbox pattern)."""
    import contextlib as _cl

    @_cl.contextmanager
    def _tx():
        conn = connect()
        with _lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    return _tx()
