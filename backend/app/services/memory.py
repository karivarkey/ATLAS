"""Layer B/C memory: chunking, ingestion outbox, hybrid retrieval, scopes.

Vector store is SQLite (BLOB float32, numpy cosine) — a justified equivalent
to pgvector for this single-node app: zero extra services, transactional with
the archive, hermetic tests. Swap via this module's functions if needed.
"""
from __future__ import annotations

import array
import json
import logging
import math
import re

import numpy as np

from .. import db

log = logging.getLogger("atlas.memory")

DEFAULT_SCOPE = "user:default"
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200
MAX_CHUNKS_PER_MESSAGE = 20
RETRIEVAL_BUDGET_CHARS = 4000
RETRIEVAL_TOP_K = 8


def scope_of(conversation_id: str) -> str:
    try:
        row = db.query_one("SELECT scope_id FROM conversations WHERE id = ?", (conversation_id,))
        return (row["scope_id"] if row and row["scope_id"] else DEFAULT_SCOPE)
    except Exception:
        return DEFAULT_SCOPE


def is_excluded(conversation_id: str) -> bool:
    try:
        row = db.query_one("SELECT memory_excluded FROM conversations WHERE id = ?", (conversation_id,))
        return bool(row and row["memory_excluded"])
    except Exception:
        return False


def chunk_text(text: str) -> list[tuple[str, int]]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [(text, 0)]
    out, start, idx = [], 0, 0
    while start < len(text) and idx < MAX_CHUNKS_PER_MESSAGE:
        end = min(len(text), start + CHUNK_SIZE)
        # break on whitespace where feasible
        if end < len(text):
            cut = text.rfind(" ", start, end)
            if cut > start + CHUNK_SIZE // 2:
                end = cut
        out.append((text[start:end].strip(), start))
        idx += 1
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [(t, s) for t, s in out if t]


def _chunk_rows(conversation_id: str, message_id: str, role: str, content: str,
                execution_id: str = "") -> list[tuple]:
    if is_excluded(conversation_id):
        return []
    scope = scope_of(conversation_id)
    now = db.now_ms()
    rows = []
    for i, (text, start) in enumerate(chunk_text(content)):
        cid = f"msg:{message_id}:{i}"
        rows.append((cid, conversation_id, message_id, execution_id, "", scope,
                     "message" if role != "tool" else "tool", text, start,
                     None, "", 0, 0, now))
    return rows


def ingest_message(conversation_id: str, message_id: str, role: str,
                   content: str, execution_id: str = "") -> int:
    """Create deterministic chunks + outbox jobs. Idempotent (INSERT OR IGNORE)."""
    if role not in ("user", "assistant") or not (content or "").strip():
        return 0
    rows = _chunk_rows(conversation_id, message_id, role, content, execution_id)
    if not rows:
        return 0
    now = db.now_ms()
    n = 0
    with db.transaction() as conn:
        for r in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO memory_chunks (id, conversation_id, message_id,"
                " execution_id, record_id, scope_id, kind, text, char_start, embedding,"
                " embedding_model, embedding_dim, indexed_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", r)
            if cur.rowcount:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_index_queue (chunk_id, status, created_at, updated_at)"
                    " VALUES (?, 'pending', ?, ?)", (r[0], now, now))
                n += 1
    return n


def reindex_conversation(conversation_id: str, reset_embeddings: bool = False) -> int:
    """(Re)create chunks for a conversation. Deterministic ids → no duplicates."""
    if is_excluded(conversation_id):
        return 0
    rows = db.query(
        "SELECT id, role, content, execution_id FROM messages WHERE conversation_id = ?"
        " AND role IN ('user', 'assistant')",
        (conversation_id,))
    n = 0
    for m in rows:
        if reset_embeddings:
            for i, _ in enumerate(chunk_text(m["content"] or "")):
                db.execute("UPDATE memory_chunks SET embedding=NULL, indexed_at=0 WHERE id=?",
                           (f"msg:{m['id']}:{i}",))
                _requeue(f"msg:{m['id']}:{i}")
        n += ingest_message(conversation_id, m["id"], m["role"], m["content"] or "",
                            m["execution_id"] or "")
    return n


def _requeue(chunk_id: str) -> None:
    now = db.now_ms()
    db.execute(
        "INSERT INTO memory_index_queue (chunk_id, status, created_at, updated_at)"
        " VALUES (?, 'pending', ?, ?)"
        " ON CONFLICT(chunk_id) DO UPDATE SET status='pending', attempts=0,"
        " next_try_at=0, updated_at=excluded.updated_at",
        (chunk_id, now, now))


# ---------------- lexical ----------------

def search_lexical(query: str, scope: str, limit: int = 20,
                   conversation_id: str = "", since: int = 0) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    # Exact-phrase bonus handled in ranking; FTS5 handles tokens.
    sql = ("SELECT c.id, c.conversation_id, c.message_id, c.kind, c.text, c.created_at,"
           " bm25(memory_chunks_fts) AS rank"
           " FROM memory_chunks_fts f JOIN memory_chunks c ON c.rowid = f.rowid"
           " WHERE memory_chunks_fts MATCH ? AND c.scope_id = ?")
    params: list = [_fts_query(query), scope]
    if conversation_id:
        sql += " AND c.conversation_id = ?"
        params.append(conversation_id)
    if since:
        sql += " AND c.created_at >= ?"
        params.append(since)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        rows = db.query(sql, tuple(params))
    except Exception as exc:  # malformed FTS syntax → literal fallback
        log.info("fts fallback: %s", exc)
        rows = db.query(
            "SELECT id, conversation_id, message_id, kind, text, created_at, 0 AS rank"
            " FROM memory_chunks WHERE scope_id = ? AND text LIKE ?"
            " ORDER BY created_at DESC LIMIT ?",
            (scope, f"%{query[:100]}%", limit))
    out = []
    for r in rows:
        score = 1.0 / (1.0 + abs(float(r["rank"] or 0)))
        if query.lower() in (r["text"] or "").lower():
            score += 0.5  # exact-substring bonus
        out.append({"chunk_id": r["id"], "conversation_id": r["conversation_id"],
                    "message_id": r["message_id"], "kind": r["kind"],
                    "text": r["text"], "created_at": r["created_at"],
                    "score": score, "match": "lexical"})
    return out


def _fts_query(query: str) -> str:
    # Quote phrases, escape FTS operators per token.
    phrases = re.findall(r'"([^"]+)"', query)
    rest = re.sub(r'"[^"]*"', " ", query)
    tokens = [re.sub(r"[^A-Za-z0-9_@#./+-]+", " ", t).strip() for t in rest.split()]
    parts = [f'"{p}"' for p in phrases if p.strip()]
    parts += [f'"{t}"*' if len(t) < 12 else f'"{t}"' for t in tokens if t]
    return " OR ".join(parts) if parts else '""'


# ---------------- vector ----------------

def _to_blob(vec: list[float]) -> bytes:
    a = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(a))
    if n > 0:
        a = a / n
    return a.tobytes()


def _from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


def search_vector(query_vec: list[float], scope: str, limit: int = 20,
                  conversation_id: str = "", since: int = 0) -> list[dict]:
    q = np.asarray(query_vec, dtype=np.float32)
    n = float(np.linalg.norm(q))
    if n == 0:
        return []
    q = q / n
    sql = ("SELECT id, conversation_id, message_id, kind, text, created_at, embedding"
           " FROM memory_chunks WHERE scope_id = ? AND embedding IS NOT NULL")
    params: list = [scope]
    if conversation_id:
        sql += " AND conversation_id = ?"
        params.append(conversation_id)
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    sql += " ORDER BY created_at DESC LIMIT 2000"
    rows = db.query(sql, tuple(params))
    scored = []
    for r in rows:
        try:
            v = _from_blob(r["embedding"])
        except Exception:
            continue
        if v.shape != q.shape:
            continue  # never mix embedding spaces
        scored.append((float(v @ q), r))
    scored.sort(key=lambda x: -x[0])
    return [{"chunk_id": r["id"], "conversation_id": r["conversation_id"],
             "message_id": r["message_id"], "kind": r["kind"],
             "text": r["text"], "created_at": r["created_at"],
             "score": max(0.0, s), "match": "vector"}
            for s, r in scored[:limit]]


# ---------------- hybrid ----------------

def hybrid_search(query: str, query_vec: list[float] | None, scope: str,
                  limit: int = 8, conversation_id: str = "",
                  since: int = 0, kinds: str = "") -> list[dict]:
    """Reciprocal-rank fusion of lexical + vector, with recency + dedupe."""
    lex = search_lexical(query, scope, 20, conversation_id, since)
    vec = search_vector(query_vec, scope, 20, conversation_id, since) if query_vec else []
    fused: dict[str, dict] = {}
    for rank, hit in enumerate(lex):
        f = fused.setdefault(hit["chunk_id"], {**hit, "rrf": 0.0, "via": []})
        f["rrf"] += 1.0 / (60 + rank)
        f["via"].append("lexical")
    for rank, hit in enumerate(vec):
        f = fused.setdefault(hit["chunk_id"], {**hit, "rrf": 0.0, "via": []})
        f["rrf"] += 1.0 / (60 + rank)
        f["via"].append("vector")
    now = db.now_ms()
    results = []
    for h in fused.values():
        if kinds and h["kind"] not in kinds.split(","):
            continue
        age_days = max(0.0, (now - (h["created_at"] or now)) / 86400000.0)
        recency = 1.0 / (1.0 + age_days / 30.0)
        score = h["rrf"] * (0.7 + 0.3 * recency)
        if len(h["via"]) > 1:
            score *= 1.25  # agreed by both paths
        results.append({**h, "score": round(score, 4), "match": "+".join(h["via"])})
    # near-duplicate suppression on normalized text
    seen, deduped = set(), []
    for h in sorted(results, key=lambda x: -x["score"]):
        key = re.sub(r"\s+", " ", (h["text"] or "").lower())[:200]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
        if len(deduped) >= limit:
            break
    return deduped


def build_memory_context(conversation_id: str, user_text: str,
                         query_vec: list[float] | None = None) -> tuple[str, list[dict]]:
    """Bounded context for the agent: soul + user + hybrid hits with provenance."""
    from . import memory_files

    if is_excluded(conversation_id):
        return "", []
    scope = scope_of(conversation_id)
    parts: list[str] = []
    soul = memory_files.read_file("SOUL.md")
    if soul:
        parts.append(f"[operating principles]\n{soul[:2500]}")
    user = memory_files.read_file("USER.md")
    if user:
        parts.append(f"[user context]\n{user[:2500]}")
    hits = hybrid_search(user_text, query_vec, scope, limit=RETRIEVAL_TOP_K)
    refs = []
    chars = 0
    for h in hits:
        snippet = (h["text"] or "")[:600]
        if chars + len(snippet) > RETRIEVAL_BUDGET_CHARS:
            break
        chars += len(snippet)
        parts.append(f"[memory {h['match']} score={h['score']} src={h['conversation_id']}#{h['message_id']}]\n{snippet}")
        refs.append({"chunk_id": h["chunk_id"], "score": h["score"]})
    return ("\n\n".join(parts), refs)


# ---------------- agent-facing tools ----------------

async def run_memory_tool(name: str, args: dict, scope: str) -> dict:
    if name == "search_memory":
        from . import embeddings as emb
        q = str(args.get("query", ""))[:500]
        vec = None
        try:
            vecs = await emb.embed_batch([q])
            vec = vecs[0] if vecs else None
        except Exception:
            vec = None  # lexical still works
        hits = hybrid_search(q, vec, scope, limit=int(args.get("limit", 8) or 8))
        if not hits:
            return {"ok": True, "output": "no memories found"}
        lines = [f"- [{h['match']} {h['score']}] {h['conversation_id']}#{h['message_id']}: "
                 f"{(h['text'] or '')[:400]}" for h in hits]
        return {"ok": True, "output": "\n".join(lines)}
    if name == "search_chat_history":
        hits = search_lexical(str(args.get("query", ""))[:500], scope,
                              limit=int(args.get("limit", 10) or 10),
                              conversation_id=str(args.get("conversation_id", "")))
        if not hits:
            return {"ok": True, "output": "no matches in chat history"}
        lines = [f"- {h['conversation_id']}#{h['message_id']}: {(h['text'] or '')[:400]}"
                 for h in hits]
        return {"ok": True, "output": "\n".join(lines)}
    if name == "get_source_message":
        mid = str(args.get("message_id", ""))
        row = db.query_one("SELECT conversation_id, role, content, created_at FROM messages WHERE id = ?",
                           (mid,))
        if not row:
            return {"ok": False, "output": "message not found"}
        conv = db.query_one("SELECT scope_id, memory_excluded FROM conversations WHERE id = ?",
                            (row["conversation_id"],))
        if not conv or (conv["scope_id"] or DEFAULT_SCOPE) != scope or conv["memory_excluded"]:
            return {"ok": False, "output": "not in your authorized scope"}
        return {"ok": True, "output": f"[{row['conversation_id']} {row['role']}]\n{row['content'][:6000]}"}
    if name == "get_conversation_context":
        cid = str(args.get("conversation_id", ""))
        if scope_of(cid) != scope or is_excluded(cid):
            return {"ok": False, "output": "not in your authorized scope"}
        rows = db.query("SELECT role, content FROM messages WHERE conversation_id = ?"
                        " ORDER BY created_at ASC LIMIT 40", (cid,))
        out = "\n".join(f"[{r['role']}] {(r['content'] or '')[:600]}" for r in rows)
        return {"ok": True, "output": out[:8000] or "(empty)"}
    if name == "get_memory_record":
        from . import dream as _dream
        rec = _dream.get_record(str(args.get("memory_id", "")), scope)
        if not rec:
            return {"ok": False, "output": "record not found or out of scope"}
        return {"ok": True, "output": json.dumps(rec, indent=1)[:4000]}
    return {"ok": False, "output": f"unknown memory tool: {name}"}


# ---------------- maintenance ----------------

def delete_conversation_cascade(conversation_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM memory_index_queue WHERE chunk_id IN"
                     " (SELECT id FROM memory_chunks WHERE conversation_id = ?)", (conversation_id,))
        conn.execute("DELETE FROM memory_chunks WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM execution_memory_refs WHERE execution_id IN"
                     " (SELECT id FROM executions WHERE conversation_id = ?)", (conversation_id,))


def set_excluded(conversation_id: str, excluded: bool) -> None:
    db.ensure_column("conversations", "memory_excluded", "INTEGER NOT NULL DEFAULT 0")
    db.ensure_column("conversations", "scope_id", f"TEXT NOT NULL DEFAULT '{DEFAULT_SCOPE}'")
    db.execute("UPDATE conversations SET memory_excluded = ? WHERE id = ?",
               (1 if excluded else 0, conversation_id))
    if excluded:
        with db.transaction() as conn:
            conn.execute("DELETE FROM memory_index_queue WHERE chunk_id IN"
                         " (SELECT id FROM memory_chunks WHERE conversation_id = ?)", (conversation_id,))
            conn.execute("DELETE FROM memory_chunks WHERE conversation_id = ?", (conversation_id,))
    else:
        reindex_conversation(conversation_id)
