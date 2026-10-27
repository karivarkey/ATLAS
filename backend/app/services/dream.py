"""Dream Engine: autonomous offline consolidation (human-like dreaming). [REVAMPED]

queued → collecting → analyzing → proposing → validating → applying → completed
(+ failed / cancelled / approval_required). Idempotent via idempotency keys.
Direct-write: facts go active, USER.md auto-updates, SOUL.md appends directly.
No confirmations — a dream files everything and completes.
"""
from __future__ import annotations

import asyncio
import json
import logging

from .. import db, events
from . import embeddings as emb
from . import memory as mem
from . import memory_files as mf

log = logging.getLogger("atlas.dream")

AUTO_CONFIDENCE = 0.7
TYPES = {"preference", "project_fact", "decision", "constraint", "episode",
         "outcome", "lesson", "question", "rule_proposal"}


def _ensure_columns() -> None:
    """Idempotent migrations for dream segregation (old DB files)."""
    try:
        db.ensure_column("dream_jobs", "thinking", "TEXT NOT NULL DEFAULT ''")
        db.ensure_column("dream_jobs", "ideas_json", "TEXT NOT NULL DEFAULT '[]'")
        db.ensure_column("dream_jobs", "questions_json", "TEXT NOT NULL DEFAULT '[]'")
        db.ensure_column("memory_records", "dream_job_id", "TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass


def _job(job_id: str) -> dict | None:
    r = db.query_one("SELECT * FROM dream_jobs WHERE id = ?", (job_id,))
    return dict(r) if r else None


def _set_status(job_id: str, status: str, **kw) -> None:
    db.execute("UPDATE dream_jobs SET status = ?, updated_at = ? WHERE id = ?",
               (status, db.now_ms(), job_id))
    for k, v in kw.items():
        db.execute(f"UPDATE dream_jobs SET {k} = ?, updated_at = ? WHERE id = ?",
                   (v, db.now_ms(), job_id))


def trigger(trigger: str = "manual", conversation_ids: list[str] | None = None,
            idempotency_key: str | None = None, model: str = "") -> dict:
    _ensure_columns()
    if idempotency_key:
        existing = db.query_one("SELECT * FROM dream_jobs WHERE idempotency_key = ?",
                                (idempotency_key,))
        if existing:
            return {"job_id": existing["id"], "status": existing["status"], "deduped": True}
    jid = db.new_id("dream")
    now = db.now_ms()
    convs = conversation_ids or []
    wm_from = db.query_one(
        "SELECT COALESCE(MAX(watermark_to), 0) AS w FROM dream_jobs WHERE status='completed'") or {"w": 0}
    db.execute(
        "INSERT INTO dream_jobs (id, idempotency_key, status, trigger, watermark_from,"
        " watermark_to, conversation_ids, model, stats_json, error, created_at, updated_at, ended_at)"
        " VALUES (?, ?, 'queued', ?, ?, 0, ?, ?, '{}', '', ?, ?, 0)",
        (jid, idempotency_key, trigger, int(wm_from["w"]), json.dumps(convs), model, now, now))
    try:
        asyncio.get_running_loop().create_task(_run(jid))
    except RuntimeError:
        pass  # no running loop (tests drive _run explicitly)
    return {"job_id": jid, "status": "queued", "deduped": False}


def get_record(record_id: str, scope: str) -> dict | None:
    r = db.query_one("SELECT * FROM memory_records WHERE id = ? AND scope_id = ?", (record_id, scope))
    return dict(r) if r else None


def job_detail(job_id: str) -> dict | None:
    """Full dream card payload: job + thinking + ideas/questions + effects."""
    _ensure_columns()
    job = _job(job_id)
    if not job:
        return None
    try:
        proposals = [dict(r) for r in db.query(
            "SELECT * FROM memory_proposals WHERE job_id = ? ORDER BY created_at ASC LIMIT 100",
            (job_id,))]
    except Exception:
        proposals = []
    try:
        records = [dict(r) for r in db.query(
            "SELECT * FROM memory_records WHERE dream_job_id = ? ORDER BY created_at ASC LIMIT 100",
            (job_id,))]
    except Exception:
        records = []
    try:
        import json as _j
        ideas = _j.loads(job.get("ideas_json") or "[]")
        questions = _j.loads(job.get("questions_json") or "[]")
    except Exception:
        ideas, questions = [], []
    return {"job": job, "thinking": job.get("thinking") or "",
            "ideas": ideas, "questions": questions,
            "proposals": proposals, "records": records}


def pending_dream_questions(limit: int = 10) -> list[dict]:
    """Open questions from recent completed dreams, newest dream first.

    These are the AI's own curiosities — the agent may steer a lull/small-talk
    moment toward one, like a human would. Rotation/dedupe is handled by the
    caller via notifications (one ask per question per conversation)."""
    _ensure_columns()
    try:
        jobs = db.query("SELECT id, questions_json, created_at FROM dream_jobs"
                        " WHERE status='completed' AND questions_json NOT IN ('', '[]')"
                        " ORDER BY created_at DESC LIMIT 10")
    except Exception:
        return []
    out: list[dict] = []
    for j in jobs:
        try:
            import json as _j
            qs = _j.loads(j["questions_json"] or "[]")
        except Exception:
            continue
        for q in qs if isinstance(qs, list) else []:
            if not isinstance(q, dict):
                continue
            text = str(q.get("question", "")).strip()
            if len(text) < 8:
                continue
            out.append({"question": text[:300], "why": str(q.get("why", ""))[:300],
                        "confidence": float(q.get("confidence", 0.5) or 0.5),
                        "job_id": j["id"], "dreamed_at": j["created_at"]})
            if len(out) >= max(1, limit):
                return out
    return out


def list_records(scope: str, status: str = "", limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM memory_records WHERE scope_id = ?"
    params: list = [scope]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.query(sql, tuple(params))]


async def _run(job_id: str) -> None:
    job = _job(job_id)
    if not job or job["status"] not in ("queued",):
        return
    try:
        from . import memory_files as _mf
        try:
            _mf.read_file("SOUL.md")
            _mf.read_file("USER.md")
        except Exception:
            pass
        _set_status(job_id, "collecting")
        convs = json.loads(job["conversation_ids"] or "[]")
        msgs = _collect(convs, int(job["watermark_from"]))
        max_ts = max([m["created_at"] for m in msgs] + [int(job["watermark_from"])])
        db.execute("UPDATE dream_jobs SET watermark_to = ?, conversation_ids = ? WHERE id = ?",
                   (max_ts, json.dumps(sorted({m['conversation_id'] for m in msgs})), job_id))
        if not msgs:
            try:
                db.execute("UPDATE dream_jobs SET thinking = ?, updated_at = ? WHERE id = ?",
                           ("Nothing new since the last dream — every message was already"
                            " consolidated. All caught up.", db.now_ms(), job_id))
            except Exception:
                pass
            _finish(job_id, "completed", {"messages": 0, "note": "nothing new"})
            return
        _set_status(job_id, "analyzing")
        proposal = await _analyze(job, msgs)
        try:
            db.execute("UPDATE dream_jobs SET thinking = ?, ideas_json = ?, questions_json = ?,"
                       " updated_at = ? WHERE id = ?",
                       (str(proposal.get("thinking", ""))[:12000],
                        json.dumps(proposal.get("ideas", [])[:20])[:20000],
                        json.dumps(proposal.get("questions", [])[:20])[:20000],
                        db.now_ms(), job_id))
        except Exception:
            pass
        _set_status(job_id, "proposing")
        n_proposed = _store_proposals(job_id, proposal)
        _set_status(job_id, "validating")
        applied = _apply(job_id, proposal, job)
        _set_status(job_id, "applying")
        _finish(job_id, "completed", {"messages": len(msgs), "proposed": n_proposed,
                                      "applied_records": applied["records"],
                                      "user_md_version": applied.get("user_version"),
                                      "soul_proposals": applied["soul_proposals"],
                                      "ideas": len(proposal.get("ideas", []) or []),
                                      "questions": len(proposal.get("questions", []) or []),
                                      "thinking_chars": len(str(proposal.get("thinking", "")))})
    except asyncio.CancelledError:
        _set_status(job_id, "cancelled", error="cancelled")
    except Exception as exc:
        log.warning("dream %s failed: %s", job_id, exc)
        _finish(job_id, "failed", {"error": str(exc)[:500]})


def _finish(job_id: str, status: str, stats: dict) -> None:
    db.execute("UPDATE dream_jobs SET status=?, stats_json=?, updated_at=?, ended_at=? WHERE id=?",
               (status, json.dumps(stats), db.now_ms(), db.now_ms(), job_id))
    db.audit("dream.finish", {"job": job_id, "status": status, **stats})


def _collect(conversation_ids: list[str], since: int) -> list[dict]:
    if conversation_ids:
        ph = ",".join("?" * len(conversation_ids))
        rows = db.query(
            f"SELECT id, conversation_id, role, content, execution_id, created_at FROM messages"
            f" WHERE conversation_id IN ({ph}) AND role IN ('user','assistant')"
            f" AND created_at > ? AND content != '' ORDER BY created_at ASC LIMIT 500",
            (*conversation_ids, since))
    else:
        rows = db.query(
            "SELECT m.id, m.conversation_id, m.role, m.content, m.execution_id, m.created_at"
            " FROM messages m JOIN conversations c ON c.id = m.conversation_id"
            " WHERE m.role IN ('user','assistant') AND m.created_at > ? AND m.content != ''"
            " AND COALESCE(c.memory_excluded, 0) = 0"
            " ORDER BY m.created_at ASC LIMIT 500", (since,))
    out = []
    for r in rows:
        if r["conversation_id"] in (conversation_ids or [r["conversation_id"]]):
            c = db.query_one("SELECT memory_excluded FROM conversations WHERE id = ?",
                             (r["conversation_id"],))
            if c and c["memory_excluded"]:
                continue
        out.append(dict(r))
    return out


async def _analyze(job: dict, msgs: list[dict]) -> dict:
    """Ask the reasoning model for a JSON extraction. Stub-safe for tests."""
    from . import model_catalog, providers

    model_id = job["model"] or _default_chat_model()
    ok, model = await model_catalog.validate_model(model_id)
    if not ok:
        raise RuntimeError(f"dream model '{model_id}' unavailable")
    convo = "\n".join(f"[{m['conversation_id']} {m['role']}]: {(m['content'] or '')[:1500]}"
                      for m in msgs[-60:])
    existing = list_records(mem.scope_of(msgs[0]["conversation_id"]), status="active", limit=50)
    exist_txt = "\n".join(f"- {r['subject']} | {r['predicate']} | {r['object_value']}"
                          for r in existing)
    prompt = ("You are dreaming: autonomous offline consolidation. NO user is present. "
              "Never ask questions, never request approval, never call tools. Think freely like "
              "a human in sleep: replay the chats, notice patterns, curiosities, contradictions, "
              "unfinished threads. Ponder ideas worth exploring and open questions worth "
              "answering later. Then consolidate durable facts. "
              "Reply with ONLY a JSON object:\n"
              '{"thinking": "2-4 paragraphs of free reflection (shown in the dream card)", '
              '"ideas": [{"title": "...", "detail": "...", "confidence": 0.0-1.0}], '
              '"questions": [{"question": "...", "why": "...", "confidence": 0.0-1.0}], '
              '"facts": [{"type": "preference|project_fact|decision|constraint|episode|outcome|lesson|question", '
              '"subject": "...", "predicate": "...", "object": "...", "summary": "...", '
              '"confidence": 0.0-1.0, "sources": [<message indexes>]}], '
              '"user_updates": ["..."], "soul_proposals": [{"current": "...", "proposed": "...", '
              '"evidence": "...", "reason": "...", "confidence": 0.0-1.0}]}\n"'
              "Rules: thinking/ideas/questions are REQUIRED (may be short but never empty when "
              "messages exist). Skip trivia and one-off chatter. Mark corrections explicitly.\n\n"
              f"EXISTING:\n{exist_txt or '(none)'}\n\nMESSAGES (index:c text):\n" +
              "\n".join(f"{i}: {line}" for i, line in enumerate(convo.splitlines())))
    text = ""
    async for chunk in providers.complete(model, [{"role": "user", "content": prompt}],
                                          session_id=f"dream-{job['id']}"):
        if "text" in chunk and chunk["text"]:
            text += chunk["text"]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("dream model returned no JSON")
    try:
        data = json.loads(text[start:end + 1])
    except Exception as exc:
        raise RuntimeError(f"dream model returned invalid JSON: {exc}")
    # attach source message ids
    for f in data.get("facts", []):
        srcs = []
        for i in f.get("sources", []) if isinstance(f.get("sources"), list) else []:
            try:
                srcs.append(msgs[int(i)]["id"])
            except Exception:
                pass
        f["_message_ids"] = srcs
    return data


def _default_chat_model() -> str:
    from .. import config
    return config.DEFAULT_MODEL


def _store_proposals(job_id: str, proposal: dict) -> int:
    n = 0
    for f in proposal.get("facts", []):
        if f.get("type") not in TYPES:
            continue
        pid = db.new_id("prop")
        db.execute("INSERT INTO memory_proposals (id, job_id, kind, payload_json, evidence_json,"
                   " confidence, status, created_at) VALUES (?, ?, 'record', ?, ?, ?, 'proposed', ?)",
                   (pid, job_id, json.dumps(f), json.dumps(f.get("_message_ids", [])),
                    float(f.get("confidence", 0.5)), db.now_ms()))
        n += 1
    for s in proposal.get("soul_proposals", []):
        pid = db.new_id("prop")
        db.execute("INSERT INTO memory_proposals (id, job_id, kind, payload_json, evidence_json,"
                   " confidence, status, created_at) VALUES (?, ?, 'soul', ?, '[]', ?, 'proposed', ?)",
                   (pid, job_id, json.dumps(s), float(s.get("confidence", 0.5)), db.now_ms()))
        n += 1
    return n


def _apply(job_id: str, proposal: dict, job: dict) -> dict:
    """Direct-write: dreams never wait for confirmation. Every fact goes active
    (superseding rivals), USER.md auto-updates, SOUL proposals are appended
    straight to SOUL.md. The job completes with everything filed."""
    scope = mem.DEFAULT_SCOPE
    applied = 0
    for f in proposal.get("facts", []):
        if f.get("type") not in TYPES:
            continue
        conf = float(f.get("confidence", 0.5))
        # dedupe: same subject+predicate+object active → just confirm
        dup = db.query_one("SELECT id FROM memory_records WHERE scope_id=? AND subject=? AND predicate=?"
                           " AND object_value=? AND status='active'",
                           (scope, f.get("subject", ""), f.get("predicate", ""), f.get("object", "")))
        if dup:
            db.execute("UPDATE memory_records SET updated_at=? WHERE id=?",
                       (db.now_ms(), dup["id"]))
            _hist(dup["id"], "confirmed", {"job": job_id})
            continue
        # contradiction: same subject+predicate, different object → the dream
        # is the authority and overwrites directly, history preserves the rival.
        rival = db.query_one("SELECT * FROM memory_records WHERE scope_id=? AND subject=? AND predicate=?"
                             " AND status='active' AND object_value != ?",
                             (scope, f.get("subject", ""), f.get("predicate", ""), f.get("object", "")))
        status = "active"
        rid = db.new_id("mem")
        convs = sorted({m["conversation_id"] for m in _msgs_for(f.get("_message_ids", []))})
        db.execute(
            "INSERT INTO memory_records (id, scope_id, memory_type, subject, predicate, object_value,"
            " summary, confidence, status, valid_from, source_conversation_ids, source_message_ids,"
            " source_chunk_ids, supersedes_memory_id, dream_job_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '', ?, ?, ?)",
            (rid, scope, f["type"], f.get("subject", "")[:300], f.get("predicate", "")[:200],
             f.get("object", "")[:500], f.get("summary", "")[:1000], conf, status,
             db.now_ms(), json.dumps(convs), json.dumps(f.get("_message_ids", [])),
             job_id, db.now_ms(), db.now_ms()))
        _hist(rid, "created", {"job": job_id, "status": status})
        if rival:
            db.execute("UPDATE memory_records SET status='superseded', updated_at=? WHERE id=?",
                       (db.now_ms(), rival["id"]))
            db.execute("UPDATE memory_records SET supersedes_memory_id=? WHERE id=?",
                       (rival["id"], rid))
            _hist(rival["id"], "superseded", {"by": rid, "job": job_id})
        applied += 1
        _index_record(rid, scope, f)
    user_version = _update_user_md(proposal.get("user_updates", []), job_id)
    soul_n = _apply_soul_direct(proposal.get("soul_proposals", []), job_id)
    try:
        db.execute("UPDATE memory_proposals SET status='applied', decided_at=?, decided_by='dream'"
                   " WHERE job_id=? AND status='proposed'", (db.now_ms(), job_id))
    except Exception:
        pass
    return {"records": applied, "user_version": user_version, "soul_proposals": soul_n}


def _apply_soul_direct(proposals: list, job_id: str) -> int:
    """Append dream SOUL proposals straight to SOUL.md (no approval gate)."""
    n = 0
    for s in proposals or []:
        addition = str((s or {}).get("proposed", "")).strip()
        if not addition:
            continue
        try:
            current = mf.read_file("SOUL.md")
        except Exception:
            continue
        if addition in current:
            n += 1
            continue
        try:
            mf.write_file("SOUL.md", current.rstrip() + "\n\n" + addition + "\n",
                          source=f"dream:{job_id}")
            n += 1
        except Exception:
            continue
    return n


def _msgs_for(message_ids: list) -> list[dict]:
    out = []
    for mid in message_ids:
        r = db.query_one("SELECT id, conversation_id FROM messages WHERE id = ?", (mid,))
        if r:
            out.append(dict(r))
    return out


def _hist(record_id: str, action: str, detail: dict) -> None:
    db.execute("INSERT INTO memory_record_history (record_id, action, detail_json, created_at)"
               " VALUES (?, ?, ?, ?)", (record_id, action, json.dumps(detail), db.now_ms()))


def _index_record(rid: str, scope: str, fact: dict) -> None:
    text = f"{fact.get('subject','')} {fact.get('predicate','')} {fact.get('object','')}. {fact.get('summary','')}"[:1500]
    cid = f"rec:{rid}"
    now = db.now_ms()
    db.execute("INSERT OR IGNORE INTO memory_chunks (id, conversation_id, message_id, record_id,"
               " scope_id, kind, text, char_start, created_at)"
               " VALUES (?, '', '', ?, ?, 'record', ?, 0, ?)",
               (cid, rid, scope, text, now))
    mem._requeue(cid)


def _update_user_md(updates: list, job_id: str) -> int | None:
    updates = [str(u).strip() for u in updates if str(u).strip()][:10]
    if not updates:
        return None
    current = mf.read_file("USER.md")
    lines = current.rstrip().splitlines()
    # keep concise: cap and note overflow
    section = "\n".join(f"- {u}" for u in updates)
    if "## Learned" in current:
        new = current.rstrip() + "\n" + section + "\n"
    else:
        new = current.rstrip() + "\n\n## Learned\n" + section + "\n"
    if len(new.splitlines()) > 160:
        new = "\n".join(new.splitlines()[:160]) + "\n- …(older details in searchable memory)\n"
    v = mf.write_file("USER.md", new, source=f"dream:{job_id}")
    return v


# ---------------- scheduler ----------------

async def scheduler() -> None:
    """Daily 03:00 local run + idempotency (one completed job per day-key)."""
    import datetime as _dt
    import os as _os

    while True:
        try:
            try:
                _ensure_columns()
                for _r in db.query("SELECT id FROM dream_jobs WHERE status='queued'"
                                   " ORDER BY created_at ASC LIMIT 5"):
                    try:
                        await _run(_r["id"])
                    except Exception:
                        pass
            except Exception:
                pass
            when = _os.environ.get("ATLAS_DREAM_AT", "03:00")
            hh, mm = (when.split(":") + ["0"])[:2]
            now = _dt.datetime.now().astimezone()
            key = f"daily-{now.date().isoformat()}"
            target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if now >= target:
                existing = db.query_one("SELECT id FROM dream_jobs WHERE idempotency_key = ?", (key,))
                if not existing:
                    log.info("scheduler starting daily dream %s", key)
                    trigger("scheduled", idempotency_key=key)
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("dream scheduler error: %s", exc)
            await asyncio.sleep(60)


def maybe_trigger_event(conversation_id: str, threshold: int = 20) -> None:
    """Debounced event trigger after substantial conversations."""
    try:
        pending = db.query_one("SELECT id FROM dream_jobs WHERE status IN ('queued','collecting','analyzing',"
                               "'proposing','validating','applying') LIMIT 1")
        if pending:
            return
        last = db.query_one("SELECT COALESCE(MAX(watermark_to),0) AS w FROM dream_jobs WHERE status='completed'")
        w = int(last["w"]) if last else 0
        n = db.query_one("SELECT COUNT(*) n FROM messages WHERE conversation_id=? AND created_at>? AND role IN ('user','assistant')",
                         (conversation_id, w))
        if n and n["n"] >= threshold:
            trigger("event", [conversation_id])
    except RuntimeError:
        pass  # no running loop (tests call directly)
    except Exception as exc:
        log.info("event dream trigger skip: %s", exc)
