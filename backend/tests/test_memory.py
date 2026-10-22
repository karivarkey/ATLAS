"""Memory acceptance tests A, B, E, F (+ deletion/recovery basics).

A: immediate cross-chat lexical retrieval after commit (no embeddings needed).
A-sem: semantic retrieval once embeddings are indexed (stubbed vectors).
B: retrieval during execution (context builder + agent search_memory tool).
E: scope isolation, exclusion, deletion cascade.
F: concurrent chats + memory ops don't cross-contaminate.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app import events as ev
from app.services import embeddings, executions, indexer, memory as mem, model_catalog, providers


VOCAB = ["kiosk", "atlas", "concurrent", "authentication", "xyzz"]


async def stub_embed(texts):
    """Deterministic keyword one-hot vectors (pipeline test; real model verified separately)."""
    import numpy as np
    out = []
    for t in texts:
        v = np.zeros(len(VOCAB) + 4)
        low = t.lower()
        for i, w in enumerate(VOCAB):
            if w in low:
                v[i] = 1.0
        v[-1] = 0.5
        n = float((v ** 2).sum() ** 0.5)
        out.append((v / n).tolist())
    return out


async def fake_text(model, messages, session_id=""):
    yield {"text": "noted and stored"}


async def _ok(m):
    return True, {"id": m, "protocol": "chat_completions",
                  "endpoint": "http://x", "supports_tools": True}


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db.reset_for_tests(tmp_path)
    monkeypatch.setattr(model_catalog, "validate_model", _ok)
    monkeypatch.setattr(providers, "complete", fake_text)
    monkeypatch.setattr(embeddings, "embed_batch", stub_embed)
    yield tmp_path


@pytest.fixture()
def client(tmp_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


def _mkconv(cid, scope="user:default"):
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, workspace_id, scope_id,"
               " memory_excluded, created_at, updated_at)"
               " VALUES (?, 't', 'stub/m', ?, ?, 0, ?, ?)", (cid, cid, scope, now, now))


def _submit_and_wait(cid, text, timeout_s=30):
    async def main():
        res = await executions.submit(cid, text, "stub/m")
        eid = res["execution_id"]
        for _ in range(int(timeout_s * 10)):
            if executions.get_snapshot(eid)["status"] in ev.TERMINAL:
                break
            await asyncio.sleep(0.1)
        return eid
    return asyncio.run(main())


def test_a_immediate_lexical_cross_chat(client):
    """Test A (lexical): xyzz committed in A is searchable from B immediately."""
    _mkconv("mA")
    _mkconv("mB")
    r = client.post("/api/v1/conversations/mA/messages",
                    json={"content": "Remember the kiosk deployment uses port xyzz-9000.", "model": "stub/m"})
    assert r.status_code == 202, r.text
    # no embedding/indexer run at all — lexical must work straight after commit
    r = client.get("/api/v1/memory/search", params={"q": "xyzz-9000"})
    body = r.json()
    assert body["results"], body
    assert body["results"][0]["conversation_id"] == "mA"
    assert "xyzz-9000" in body["results"][0]["text"]


def test_a_semantic_after_indexing(client):
    """Test A (semantic): related wording finds the passage once embedded."""
    _mkconv("mA2")
    client.post("/api/v1/conversations/mA2/messages",
                json={"content": "The kiosk rollout was delayed by authentication issues.", "model": "stub/m"})
    asyncio.run(indexer._process_batch())
    r = client.get("/api/v1/memory/search", params={"q": "kiosk authentication problems"})
    body = r.json()
    assert body["semantic"] is True
    assert any(h["conversation_id"] == "mA2" for h in body["results"]), body


def test_b_retrieval_during_execution(client):
    """Test B: decision stored in A is retrieved as context in B; tool works."""
    _mkconv("bA")
    _mkconv("bB")
    client.post("/api/v1/conversations/bA/messages",
                json={"content": "Architecture decision: we chose Atlas memory over Redis.", "model": "stub/m"})
    asyncio.run(indexer._process_batch())
    ctx, refs = mem.build_memory_context("bB", "which memory backend did we choose?")
    assert "Atlas memory over Redis" in ctx, ctx
    assert refs, "expected provenance refs"
    out = asyncio.run(mem.run_memory_tool(
        "search_memory", {"query": "memory backend choice", "limit": 5}, "user:default"))
    assert out["ok"] and "Redis" in out["output"]
    src = asyncio.run(mem.run_memory_tool(
        "get_source_message", {"message_id": refs[0]["chunk_id"].split(":")[1]}, "user:default"))
    assert src["ok"] and "Redis" in src["output"]


def test_e_scope_isolation_and_exclusion(client):
    """Test E: scopes don't leak; excluded chats stay out of retrieval."""
    _mkconv("eA", scope="team-alpha")
    _mkconv("eB", scope="team-beta")
    _mkconv("eC", scope="team-alpha")
    client.post("/api/v1/conversations/eA/messages",
                json={"content": "Alpha secret project codename quasar.", "model": "stub/m"})
    client.post("/api/v1/conversations/eB/messages",
                json={"content": "Beta secret project codename pulsar.", "model": "stub/m"})
    client.post("/api/v1/conversations/eC/messages",
                json={"content": "Alpha public note about quasar timeline.", "model": "stub/m"})
    # exclude eC from memory
    r = client.patch("/api/v1/conversations/eC", json={"memory_excluded": True})
    assert r.status_code == 200
    assert r.json()["memory_excluded"] is True
    hits_a = mem.hybrid_search("quasar", None, "team-alpha")
    assert any(h["conversation_id"] == "eA" for h in hits_a)
    assert not any(h["conversation_id"] == "eC" for h in hits_a), "excluded chat leaked"
    assert not any(h["conversation_id"] == "eB" for h in hits_a), "scope leaked"
    hits_b = mem.hybrid_search("pulsar", None, "team-beta")
    assert any(h["conversation_id"] == "eB" for h in hits_b)
    hits_b2 = mem.hybrid_search("quasar", None, "team-beta")
    assert not any(h["conversation_id"] == "eA" for h in hits_b2)


def test_e_deletion_cascades(client):
    _mkconv("eD")
    client.post("/api/v1/conversations/eD/messages",
                json={"content": "Temporary fact zephyr-123.", "model": "stub/m"})
    asyncio.run(indexer._process_batch())
    assert mem.hybrid_search("zephyr-123", None, "user:default")
    assert client.delete("/api/v1/conversations/eD").status_code == 200
    assert mem.hybrid_search("zephyr-123", None, "user:default") == []
    assert db.query("SELECT id FROM memory_chunks WHERE conversation_id='eD'") == []


def test_f_concurrent_chats_and_memory(client):
    """Test F: concurrent executions + simultaneous search/index stay isolated."""
    _mkconv("fA")
    _mkconv("fB")

    async def main():
        ra = (await executions.submit("fA", "kiosk fact for A", "stub/m"))["execution_id"]
        rb = (await executions.submit("fB", "atlas fact for B", "stub/m"))["execution_id"]
        # hammer search + indexing while both run
        for _ in range(100):
            sa = executions.get_snapshot(ra)["status"]
            sb = executions.get_snapshot(rb)["status"]
            mem.hybrid_search("kiosk", None, "user:default")
            await indexer._process_batch()
            if sa in ev.TERMINAL and sb in ev.TERMINAL:
                break
            await asyncio.sleep(0.05)
        assert executions.get_snapshot(ra)["status"] == "completed"
        assert executions.get_snapshot(rb)["status"] == "completed"

    asyncio.run(main())
    hits = mem.hybrid_search("kiosk fact", None, "user:default")
    assert any(h["conversation_id"] == "fA" for h in hits)
    ma = db.query("SELECT content FROM messages WHERE conversation_id='fA' AND role='assistant'")
    mb = db.query("SELECT content FROM messages WHERE conversation_id='fB' AND role='assistant'")
    assert ma and mb
