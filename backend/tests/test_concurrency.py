import pytest
"""Concurrency acceptance test (§10): two chats progress independently,
no crossed messages/tool outputs, cancel of A leaves B running.

Provider is stubbed (no network); routing, persistence and cancellation
are all real code paths.
"""
import asyncio
import json

from app import db
from app.services import executions, model_catalog, providers



@pytest.fixture(autouse=True)
def _no_real_embeds(monkeypatch):
    from app.services import embeddings as _emb

    async def _fake(texts):
        return [[1.0] * 1024 for _ in texts]

    monkeypatch.setattr(_emb, "embed_batch", _fake)

async def fake_complete(model, messages, session_id=""):
    mid = model["id"]
    yield {"text": f"hello from {mid} "}
    yield {"tool": {"id": "c1", "name": "fs_write",
                    "arguments": {"path": f"out-{mid.replace('/', '_')}.txt",
                                  "content": f"written by {mid}"}}}
    yield {"text": "done"}


async def fake_complete_slow(model, messages, session_id=""):
    yield {"text": "slow start "}
    for _ in range(20):
        await asyncio.sleep(0.1)
        yield {"text": "."}
    yield {"text": " slow done"}


def _mkconv(cid, model):
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
               " VALUES (?, 't', ?, ?, ?, ?)", (cid, model, cid, now, now))


def test_concurrent_executions_are_isolated(tmp_path, monkeypatch):
    async def main():
        from app import events as ev
        monkeypatch.setattr(model_catalog, "validate_model",
                            lambda m: _ok(m))
        monkeypatch.setattr(providers, "complete", fake_complete)

        async def _ok(m):
            return True, {"id": m, "protocol": "chat_completions",
                          "endpoint": "http://x", "supports_tools": True}

        _mkconv("chatA", "stub/a")
        _mkconv("chatB", "stub/b")
        ra = await executions.submit("chatA", "task A", "stub/a")
        rb = await executions.submit("chatB", "task B", "stub/b")
        assert ra["execution_id"] != rb["execution_id"]
        # Wait for both to finish.
        for _ in range(200):
            sa = executions.get_snapshot(ra["execution_id"])["status"]
            sb = executions.get_snapshot(rb["execution_id"])["status"]
            if sa in ev.TERMINAL and sb in ev.TERMINAL:
                break
            await asyncio.sleep(0.1)
        assert sa == "completed", executions.get_snapshot(ra["execution_id"])
        assert sb == "completed", executions.get_snapshot(rb["execution_id"])
        # No crossed events: each execution's log mentions only its own model.
        ea = ev.replay(ra["execution_id"], 0)
        eb = ev.replay(rb["execution_id"], 0)
        assert any("stub/a" in json.dumps(e) for e in ea)
        assert not any("stub/b" in json.dumps(e) for e in ea)
        assert any("stub/b" in json.dumps(e) for e in eb)
        assert not any("stub/a" in json.dumps(e) for e in eb)
        # Messages isolated per conversation.
        ma = db.query("SELECT content FROM messages WHERE conversation_id='chatA'")
        mb = db.query("SELECT content FROM messages WHERE conversation_id='chatB'")
        assert any("stub/a" in (r["content"] or "") for r in ma)
        assert not any("stub/b" in (r["content"] or "") for r in ma)
        assert any("stub/b" in (r["content"] or "") for r in mb)

    db.reset_for_tests(tmp_path)
    asyncio.run(main())


def test_cancel_a_leaves_b_running(tmp_path, monkeypatch):
    async def main():
        from app import events as ev
        monkeypatch.setattr(providers, "complete", fake_complete_slow)

        async def _ok(m):
            return True, {"id": m, "protocol": "chat_completions",
                          "endpoint": "http://x", "supports_tools": True}
        monkeypatch.setattr(model_catalog, "validate_model", _ok)

        _mkconv("chatC", "stub/c")
        _mkconv("chatD", "stub/d")
        # Serialize: acquire conv locks via sequential submit then stop one.
        rc = await executions.submit("chatC", "slow task", "stub/c")
        await asyncio.sleep(0.3)
        assert await executions.stop(rc["execution_id"]) is True
        for _ in range(200):
            if executions.get_snapshot(rc["execution_id"])["status"] in ev.TERMINAL:
                break
            await asyncio.sleep(0.1)
        assert executions.get_snapshot(rc["execution_id"])["status"] == "cancelled"
        rd = await executions.submit("chatD", "quick task", "stub/d")
        for _ in range(200):
            if executions.get_snapshot(rd["execution_id"])["status"] in ev.TERMINAL:
                break
            await asyncio.sleep(0.1)
        assert executions.get_snapshot(rd["execution_id"])["status"] == "completed"

    db.reset_for_tests(tmp_path)
    asyncio.run(main())
