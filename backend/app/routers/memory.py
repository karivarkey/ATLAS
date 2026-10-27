from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..services import dream, embeddings, indexer
from ..services import memory as mem
from ..services import memory_files as mf

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


def _scope(conversation_id: str = "", scope: str = "") -> str:
    if scope:
        return scope
    if conversation_id:
        return mem.scope_of(conversation_id)
    return mem.DEFAULT_SCOPE


@router.get("/search")
async def search(q: str, scope: str = "", conversation_id: str = "",
                 limit: int = 8, kinds: str = ""):
    sc = _scope(conversation_id, scope)
    vec = None
    try:
        vecs = await embeddings.embed_batch([q[:2000]])
        vec = vecs[0] if vecs else None
    except Exception:
        vec = None
    conv = db.query_one("SELECT memory_excluded FROM conversations WHERE id = ?", (conversation_id,)) \
        if conversation_id else None
    if conv and conv["memory_excluded"] and not conversation_id:
        raise HTTPException(403, "conversation is excluded from memory")
    return {"results": mem.hybrid_search(q, vec, sc, limit=min(limit, 25),
                                         conversation_id="", kinds=kinds),
            "scope": sc, "semantic": vec is not None}


@router.get("/records")
def records(scope: str = "", status: str = "", limit: int = 100):
    return {"records": dream.list_records(_scope(scope=scope), status, min(limit, 200))}


@router.get("/records/{rid}")
def record(rid: str, scope: str = ""):
    rec = dream.get_record(rid, _scope(scope=scope))
    if not rec:
        raise HTTPException(404, "record not found or out of scope")
    return rec


@router.delete("/records/{rid}")
def delete_record(rid: str, scope: str = ""):
    rec = dream.get_record(rid, _scope(scope=scope))
    if not rec:
        raise HTTPException(404, "record not found or out of scope")
    db.execute("UPDATE memory_records SET status='archived', updated_at=? WHERE id = ?",
               (db.now_ms(), rid))
    db.execute("DELETE FROM memory_chunks WHERE record_id = ?", (rid,))
    db.audit("memory.record_delete", {"id": rid})
    return {"ok": True}


@router.get("/files/{name}")
def read_file(name: str):
    try:
        content = mf.read_file(name)
    except ValueError:
        raise HTTPException(404, "unknown file")
    vers = mf.versions(name)
    return {"path": name, "content": content,
            "version": vers[0]["version"] if vers else 0, "versions": vers}


@router.put("/files/{name}")
def write_file(name: str, body: dict):
    try:
        v = mf.write_file(name, body.get("content", ""), source="manual:api")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "version": v}


@router.post("/files/{name}/rollback/{version}")
def rollback(name: str, version: int):
    try:
        v = mf.rollback(name, version)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "version": v}


@router.get("/proposals")
def proposals(status: str = "proposed", limit: int = 100):
    rows = db.query("SELECT * FROM memory_proposals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status or "proposed", min(limit, 200)))
    return {"proposals": [dict(r) for r in rows]}


@router.post("/proposals/{pid}/approve")
def approve_proposal(pid: str, body: dict | None = None):
    p = db.query_one("SELECT * FROM memory_proposals WHERE id = ?", (pid,))
    if not p:
        raise HTTPException(404, "proposal not found")
    if p["status"] != "proposed":
        raise HTTPException(409, f"proposal is {p['status']}")
    import json as _json
    payload = _json.loads(p["payload_json"])
    by = (body or {}).get("by", "user")
    if p["kind"] == "soul":
        mf.write_file("SOUL.md", _soul_with(payload), source=f"approval:{by}")
    elif p["kind"] == "record":
        _apply_record_proposal(payload, p)
    db.execute("UPDATE memory_proposals SET status='applied', decided_at=?, decided_by=? WHERE id = ?",
               (db.now_ms(), by, pid))
    db.audit("memory.proposal_approve", {"id": pid, "kind": p["kind"], "by": by})
    return {"ok": True}


def _soul_with(proposal: dict) -> str:
    current = mf.read_file("SOUL.md")
    addition = str(proposal.get("proposed", "")).strip()
    if not addition:
        raise HTTPException(400, "proposal has no content")
    if addition in current:
        return current
    return current.rstrip() + f"\n\n{addition}\n"


def _apply_record_proposal(payload: dict, p) -> None:
    import json as _json
    scope = mem.DEFAULT_SCOPE
    rid = db.new_id("mem")
    db.execute(
        "INSERT INTO memory_records (id, scope_id, memory_type, subject, predicate, object_value,"
        " summary, confidence, status, valid_from, source_conversation_ids, source_message_ids,"
        " source_chunk_ids, supersedes_memory_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, '[]', ?, '[]', '', ?, ?)",
        (rid, scope, payload.get("type", "lesson"), str(payload.get("subject", ""))[:300],
         str(payload.get("predicate", ""))[:200], str(payload.get("object", ""))[:500],
         str(payload.get("summary", ""))[:1000],
         float(payload.get("confidence", 0.5)), db.now_ms(),
         _json.dumps(payload.get("_message_ids", [])), db.now_ms(), db.now_ms()))
    db.execute("INSERT INTO memory_record_history (record_id, action, detail_json, created_at)"
               " VALUES (?, 'created', ?, ?)", (rid, _json.dumps({"via_proposal": p["id"]}), db.now_ms()))


@router.post("/proposals/{pid}/reject")
def reject_proposal(pid: str, body: dict | None = None):
    p = db.query_one("SELECT * FROM memory_proposals WHERE id = ?", (pid,))
    if not p:
        raise HTTPException(404, "proposal not found")
    db.execute("UPDATE memory_proposals SET status='rejected', decided_at=?, decided_by=? WHERE id = ?",
               (db.now_ms(), (body or {}).get("by", "user"), pid))
    return {"ok": True}


@router.get("/jobs")
def jobs(limit: int = 50):
    import json as _json
    dream._ensure_columns()
    rows = db.query("SELECT id, idempotency_key, status, trigger, model, stats_json, error,"
                    " thinking, ideas_json, questions_json,"
                    " created_at, updated_at, ended_at FROM dream_jobs"
                    " ORDER BY created_at DESC LIMIT ?", (min(limit, 200),))
    out = []
    for r in rows:
        d = dict(r)
        try:
            _ideas = _json.loads(d.get("ideas_json") or "[]")
        except Exception:
            _ideas = []
        try:
            _questions = _json.loads(d.get("questions_json") or "[]")
        except Exception:
            _questions = []
        d["ideas_count"] = len(_ideas)
        d["questions_count"] = len(_questions)
        d["thinking_preview"] = (d.get("thinking") or "")[:280]
        out.append(d)
    return {"jobs": out}


@router.get("/jobs/{jid}")
def job(jid: str):
    dream._ensure_columns()
    r = db.query_one("SELECT * FROM dream_jobs WHERE id = ?", (jid,))
    if not r:
        raise HTTPException(404, "job not found")
    return dict(r)


@router.get("/jobs/{jid}/detail")
def job_detail(jid: str):
    """Full dream card payload: thinking + ideas + questions + records + proposals."""
    detail = dream.job_detail(jid)
    if not detail:
        raise HTTPException(404, "job not found")
    return detail


@router.post("/dream")
async def dream_now(body: dict | None = None):
    body = body or {}
    return dream.trigger(body.get("trigger", "manual"),
                         body.get("conversation_ids"),
                         body.get("idempotency_key"),
                         body.get("model", ""))


@router.get("/health")
def health():
    from ..services import indexer as _idx
    last = db.query_one("SELECT id, status, ended_at FROM dream_jobs ORDER BY created_at DESC LIMIT 1")
    return {"embeddings": embeddings.health(),
            "indexing": _idx.backlog(),
            "last_dream": dict(last) if last else None,
            "soul_version": (mf.versions("SOUL.md") or [{}])[0].get("version", 0),
            "user_version": (mf.versions("USER.md") or [{}])[0].get("version", 0)}


@router.get("/execution/{eid}/refs")
def execution_refs(eid: str):
    rows = db.query("SELECT r.chunk_id, r.score, c.conversation_id, c.message_id,"
                    " SUBSTR(c.text, 1, 300) AS snippet FROM execution_memory_refs r"
                    " JOIN memory_chunks c ON c.id = r.chunk_id WHERE r.execution_id = ?"
                    " ORDER BY r.score DESC", (eid,))
    return {"refs": [dict(r) for r in rows]}
