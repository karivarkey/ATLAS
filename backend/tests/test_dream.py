"""Memory acceptance tests C (dreaming), D (recovery), G (human edits), H (scheduler)."""
import asyncio
import json

from app import db
from app import events as ev
from app.services import dream, embeddings, executions, indexer, memory as mem, model_catalog, providers


async def stub_embed(texts):
    import numpy as np
    out = []
    for t in texts:
        v = np.zeros(8)
        v[0] = 1.0 if "kiosk" in t.lower() else 0.3
        v[1] = 1.0 if "redis" in t.lower() else 0.2
        out.append((v / float((v ** 2).sum() ** 0.5)).tolist())
    return out


DREAM_REPLY = json.dumps({
    "facts": [
        {"type": "preference", "subject": "user", "predicate": "likes",
         "object": "dark mode", "summary": "User said they like dark mode.",
         "confidence": 0.9, "sources": [0]},
        {"type": "decision", "subject": "atlas", "predicate": "memory_backend",
         "object": "sqlite", "summary": "Chose SQLite over Redis for memory.",
         "confidence": 0.85, "sources": [1]},
        {"type": "rule_proposal", "subject": "agent", "predicate": "verify",
         "object": "always run tests", "summary": "Low confidence draft.",
         "confidence": 0.3, "sources": [2]},
    ],
    "user_updates": ["Prefers dark mode UI."],
    "soul_proposals": [{"current": "Be helpful.", "proposed": "Verify before claiming success.",
                        "evidence": "test failures", "reason": "honesty", "confidence": 0.8}],
})


async def fake_dream_llm(model, messages, session_id=""):
    yield {"text": DREAM_REPLY}


async def fake_text(model, messages, session_id=""):
    yield {"text": "ok"}


async def _ok(m):
    return True, {"id": m, "protocol": "chat_completions",
                  "endpoint": "http://x", "supports_tools": True}


def _setup(tmp_path, monkeypatch, llm=None):
    db.reset_for_tests(tmp_path)
    monkeypatch.setattr(model_catalog, "validate_model", _ok)
    monkeypatch.setattr(providers, "complete", llm or fake_dream_llm)
    monkeypatch.setattr(embeddings, "embed_batch", stub_embed)


def _mkconv(cid):
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, workspace_id, scope_id,"
               " memory_excluded, created_at, updated_at)"
               " VALUES (?, 't', 'stub/m', ?, 'user:default', 0, ?, ?)", (cid, cid, now, now))


def test_c_dream_extracts_records_and_updates_user(tmp_path, monkeypatch):
    """Test C: facts extracted, no dupes on rerun, USER.md updated, SOUL proposed not applied."""
    _setup(tmp_path, monkeypatch)
    _mkconv("dA")
    now = db.now_ms()
    for i, text in enumerate(["I like dark mode, please remember.", "Decision: SQLite over Redis for memory.", "Just chatting about lunch."]):
        mid = db.new_id("msg")
        db.execute("INSERT INTO messages (id, conversation_id, role, content, model, parts_json,"
                   " execution_id, status, created_at) VALUES (?, 'dA', 'user', ?, '', '[]', '', 'done', ?)",
                   (mid, text, now + i))
        mem.ingest_message("dA", mid, "user", text)
    res = dream.trigger("manual", ["dA"])
    jid = res["job_id"]
    asyncio.run(dream._run(jid))
    job = dream._job(jid)
    assert job["status"] == "completed", job
    recs = dream.list_records("user:default", status="active")
    kinds = {(r["memory_type"], r["subject"], r["predicate"]) for r in recs}
    assert ("preference", "user", "likes") in kinds
    assert ("decision", "atlas", "memory_backend") in kinds
    # low-confidence rule_proposal stays proposed, not active
    props = db.query("SELECT * FROM memory_proposals WHERE job_id = ?", (jid,))
    assert any(p["kind"] == "soul" and p["status"] == "proposed" for p in props)
    user = db.query_one("SELECT content FROM memory_file_versions WHERE path='USER.md'"
                        " ORDER BY version DESC LIMIT 1")
    assert "dark mode" in user["content"].lower()
    soul = db.query_one("SELECT content FROM memory_file_versions WHERE path='SOUL.md'"
                        " ORDER BY version DESC LIMIT 1")
    assert "Verify before claiming success." not in soul["content"]  # requires approval
    # rerun over same watermark: nothing new → completed with 0 messages
    res2 = dream.trigger("manual", ["dA"])
    asyncio.run(dream._run(res2["job_id"]))
    job2 = dream._job(res2["job_id"])
    assert job2["status"] == "completed"
    recs2 = dream.list_records("user:default", status="active")
    assert len(recs2) == len(recs), "duplicate facts added on rerun"


def test_c_contradiction_supersedes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _mkconv("dB")
    now = db.now_ms()
    mid = db.new_id("msg")
    db.execute("INSERT INTO messages (id, conversation_id, role, content, model, parts_json,"
               " execution_id, status, created_at) VALUES (?, 'dB', 'user', 'x', '', '[]', '', 'done', ?)",
               (mid, now))
    mem.ingest_message("dB", mid, "user", "x")
    res = dream.trigger("manual", ["dB"])
    asyncio.run(dream._run(res["job_id"]))
    # second dream sees a NEW message changing the decision with higher confidence
    mid2 = db.new_id("msg")
    db.execute("INSERT INTO messages (id, conversation_id, role, content, model, parts_json,"
               " execution_id, status, created_at) VALUES (?, 'dB', 'user', 'update', '', '[]', '', 'done', ?)",
               (mid2, db.now_ms()))
    mem.ingest_message("dB", mid2, "user", "update")
    async def fake2(model, messages, session_id=""):
        yield {"text": json.dumps({"facts": [
            {"type": "decision", "subject": "atlas", "predicate": "memory_backend",
             "object": "postgres", "summary": "Migrated to Postgres.",
             "confidence": 0.95, "sources": [0]}],
            "user_updates": [], "soul_proposals": []})}
    monkeypatch.setattr(providers, "complete", fake2)
    res2 = dream.trigger("manual", ["dB"])
    asyncio.run(dream._run(res2["job_id"]))
    old = db.query("SELECT status FROM memory_records WHERE object_value='sqlite'")
    new = db.query("SELECT status FROM memory_records WHERE object_value='postgres'")
    assert old and old[0]["status"] == "superseded"
    assert new and new[0]["status"] == "active"


def test_d_indexer_recovery_no_dupes(tmp_path, monkeypatch):
    """Test D: crashed processing job resumes; deterministic ids prevent dupes."""
    _setup(tmp_path, monkeypatch)
    _mkconv("rA")
    mid = db.new_id("msg")
    db.execute("INSERT INTO messages (id, conversation_id, role, content, model, parts_json,"
               " execution_id, status, created_at) VALUES (?, 'rA', 'user', ?, '', '[]', '', 'done', ?)",
               (mid, "recovery test content kiosk", db.now_ms()))
    n = mem.ingest_message("rA", mid, "user", "recovery test content kiosk")
    assert n == 1
    # simulate crash: claim then abandon
    claimed = indexer._claim()
    assert claimed
    rec = indexer.recover()
    assert rec["reset_processing"] == 1
    asyncio.run(indexer._process_batch())
    asyncio.run(indexer._process_batch())
    n_chunks = db.query("SELECT COUNT(*) n FROM memory_chunks")[0]["n"]
    assert n_chunks == 1, "duplicate chunk created"
    emb = db.query_one("SELECT embedding FROM memory_chunks")["embedding"]
    assert emb is not None


def test_d_dream_failure_retry_and_rollback(tmp_path, monkeypatch):
    """Test D: failed dream can retry; bad USER.md write rejected, rollback works."""
    _setup(tmp_path, monkeypatch, llm=fake_text)  # non-JSON → dream fails honestly
    _mkconv("rB")
    mid = db.new_id("msg")
    db.execute("INSERT INTO messages (id, conversation_id, role, content, model, parts_json,"
               " execution_id, status, created_at) VALUES (?, 'rB', 'user', 'hi', '', '[]', '', 'done', ?)",
               (mid, db.now_ms()))
    mem.ingest_message("rB", mid, "user", "hi")
    res = dream.trigger("manual", ["rB"])
    asyncio.run(dream._run(res["job_id"]))
    assert dream._job(res["job_id"])["status"] == "failed"
    # retry with working LLM succeeds (same idempotency not reused — new job)
    monkeypatch.setattr(providers, "complete", fake_dream_llm)
    res2 = dream.trigger("manual", ["rB"])
    asyncio.run(dream._run(res2["job_id"]))
    assert dream._job(res2["job_id"])["status"] == "completed"
    # malformed/empty file write rejected; rollback restores previous version
    from app.services import memory_files as mf
    before = mf.read_file("USER.md")
    try:
        mf.write_file("USER.md", "x")
        raise SystemExit("should have rejected")
    except ValueError:
        pass
    assert mf.read_file("USER.md") == before
    v0 = mf.versions("USER.md")[0]["version"]
    mf.write_file("USER.md", before + "\n- Manual note for rollback test.\n", source="manual")
    assert "rollback test" in mf.read_file("USER.md")
    mf.rollback("USER.md", v0)
    assert "rollback test" not in mf.read_file("USER.md")


def test_g_human_edit_reconciled(tmp_path, monkeypatch):
    """Test G: manual file edits are adopted as new versions, not overwritten."""
    _setup(tmp_path, monkeypatch)
    from app.services import memory_files as mf
    mf.read_file("SOUL.md")  # mirrors startup seeding
    v0 = mf.versions("SOUL.md")[0]["version"]
    p = mf.path_of("SOUL.md")
    on_disk = p.read_text(encoding="utf-8")
    p.write_text(on_disk + "\n- Human rule: always greet the user warmly.\n", encoding="utf-8")
    content = mf.read_file("SOUL.md")  # triggers reconcile
    assert "greet the user warmly" in content
    vers = mf.versions("SOUL.md")
    assert vers[0]["version"] == v0 + 1 and vers[0]["source"] == "manual"
    # DB agrees with disk
    assert mf.read_file("SOUL.md") == p.read_text(encoding="utf-8")


def test_h_scheduler_idempotent_manual_trigger(tmp_path, monkeypatch):
    """Test H: same idempotency key dedupes; scheduler honors daily key."""
    _setup(tmp_path, monkeypatch)
    r1 = dream.trigger("scheduled", [], idempotency_key="daily-2026-10-23")
    r2 = dream.trigger("scheduled", [], idempotency_key="daily-2026-10-23")
    assert r2["deduped"] is True and r1["job_id"] == r2["job_id"]
    asyncio.run(dream._run(r1["job_id"]))  # empty but completes
    assert dream._job(r1["job_id"])["status"] == "completed"
