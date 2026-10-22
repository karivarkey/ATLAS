"""Durable async indexing worker: claims outbox jobs, embeds, stores.

- Crash-safe: jobs claimed processing→ reset to pending on startup.
- Idempotent: deterministic chunk ids + INSERT OR IGNORE → no duplicates.
- Bounded: batch claims, per-job retry cap with backoff, failed-job visibility.
- Never blocks chat: runs as a background asyncio task; lexical works regardless.
"""
from __future__ import annotations

import asyncio
import logging

from .. import db
from . import embeddings as emb
from . import memory as mem

log = logging.getLogger("atlas.indexer")

CLAIM_BATCH = 32
MAX_ATTEMPTS = 5
_running = False


def recover() -> dict:
    """Reset orphaned processing jobs; reconcile messages missing chunks."""
    prows = db.query("SELECT chunk_id FROM memory_index_queue WHERE status = 'processing'")
    for r in prows:
        db.execute("UPDATE memory_index_queue SET status='pending', updated_at=? WHERE chunk_id=?",
                   (db.now_ms(), r["chunk_id"]))
    # Reconcile: chunk-less user/assistant messages get ingested.
    missing = db.query(
        "SELECT m.conversation_id, m.id, m.role, m.content, m.execution_id FROM messages m"
        " LEFT JOIN memory_chunks c ON c.message_id = m.id"
        " WHERE m.role IN ('user','assistant') AND c.id IS NULL"
        " AND m.content != '' LIMIT 500")
    ingested = 0
    for m in missing:
        try:
            ingested += mem.ingest_message(m["conversation_id"], m["id"], m["role"],
                                           m["content"] or "", m["execution_id"] or "")
        except Exception as exc:
            log.info("reconcile skip %s: %s", m["id"], exc)
    return {"reset_processing": len(prows), "reconciled_messages": ingested}


def backlog() -> dict:
    p = db.query_one("SELECT COUNT(*) n FROM memory_index_queue WHERE status='pending'"
                     " AND next_try_at <= ?", (db.now_ms(),)) or {"n": 0}
    f = db.query_one("SELECT COUNT(*) n FROM memory_index_queue WHERE status='failed'") or {"n": 0}
    return {"pending": p["n"], "failed": f["n"],
            "chunks": (db.query_one("SELECT COUNT(*) n FROM memory_chunks") or {"n": 0})["n"],
            "embedded": (db.query_one("SELECT COUNT(*) n FROM memory_chunks WHERE embedding IS NOT NULL")
                         or {"n": 0})["n"]}


def _claim() -> list[str]:
    now = db.now_ms()
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT chunk_id FROM memory_index_queue WHERE status='pending' AND next_try_at <= ?"
            " ORDER BY id ASC LIMIT ?", (now, CLAIM_BATCH)).fetchall()
        ids = [r[0] for r in rows]
        for cid in ids:
            conn.execute("UPDATE memory_index_queue SET status='processing', updated_at=? WHERE chunk_id=?",
                         (now, cid))
    return ids


def _finish(chunk_id: str, ok: bool, error: str = "") -> None:
    now = db.now_ms()
    if ok:
        db.execute("DELETE FROM memory_index_queue WHERE chunk_id = ?", (chunk_id,))
        return
    row = db.query_one("SELECT attempts FROM memory_index_queue WHERE chunk_id = ?", (chunk_id,))
    attempts = (row["attempts"] if row else 0) + 1
    if attempts >= MAX_ATTEMPTS:
        db.execute("UPDATE memory_index_queue SET status='failed', attempts=?, last_error=?,"
                   " updated_at=? WHERE chunk_id=?", (attempts, error[:500], now, chunk_id))
    else:
        backoff = min(3600, 30 * (2 ** attempts))
        db.execute("UPDATE memory_index_queue SET status='pending', attempts=?, last_error=?,"
                   " next_try_at=?, updated_at=? WHERE chunk_id=?",
                   (attempts, error[:500], now + backoff * 1000, now, chunk_id))


async def _process_batch() -> int:
    ids = _claim()
    if not ids:
        return 0
    rows = []
    for cid in ids:
        r = db.query_one("SELECT id, text FROM memory_chunks WHERE id = ?", (cid,))
        if r:
            rows.append(r)
        else:
            _finish(cid, True)  # chunk gone (deleted) — drop job
    if not rows:
        return 0
    try:
        vecs = await emb.embed_batch([r["text"] for r in rows])
    except Exception as exc:
        for r in rows:
            _finish(r["id"], False, str(exc)[:300])
        return 0
    for r, v in zip(rows, vecs):
        try:
            db.execute("UPDATE memory_chunks SET embedding=?, embedding_model=?,"
                       " embedding_dim=?, indexed_at=? WHERE id=?",
                       (mem._to_blob(v), emb.MODEL, emb.DIM, db.now_ms(), r["id"]))
            _finish(r["id"], True)
        except Exception as exc:
            _finish(r["id"], False, str(exc)[:300])
    return len(rows)


async def worker() -> None:
    global _running
    if _running:
        return
    _running = True
    log.info("indexer worker started")
    try:
        while True:
            try:
                n = await _process_batch()
            except Exception as exc:
                log.warning("indexer batch error: %s", exc)
                n = 0
            await asyncio.sleep(2 if n else 5)
    finally:
        _running = False
