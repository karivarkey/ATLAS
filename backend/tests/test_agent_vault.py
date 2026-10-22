

"""Agent loop drives vault writes with version tracking (execution_id plumb-through)."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import db, events
from app.services import executions, model_catalog, providers


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    db.reset_for_tests(tmp_path)
    monkeypatch.setenv("ATLAS_VAULT_DIR", str(tmp_path / "vault"))

    async def _ok(m):
        return True, {"id": m, "protocol": "chat_completions",
                      "endpoint": "http://x", "supports_tools": True}

    async def _fake(texts):
        return [[1.0] * 8 for _ in texts]

    monkeypatch.setattr(model_catalog, "validate_model", _ok)
    from app.services import embeddings as _emb
    monkeypatch.setattr(_emb, "embed_batch", _fake)
    yield tmp_path


@pytest.fixture()
def client(tmp_env):
    from app.main import app
    with TestClient(app) as c:
        yield c


def _mkconv(cid="tc1"):
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
               " VALUES (?, 't', 'stub/m', ?, ?, ?)", (cid, cid, now, now))


def test_agent_drives_vault_write_with_version(client, tmp_env, monkeypatch):
    """Agent loop passes execution_id through: note_read records version,
    note_write succeeds, second write with the old version conflicts."""
    import json as _json

    async def main():
        calls = {"n": 0}
        created = client.post("/api/v1/notes",
                              json={"path": "Agent.md", "content": "# A\n\nv1\n"}).json()
        h1 = created["content_hash"]

        async def scripted2(model, messages, session_id=""):
            calls["n"] += 1
            if calls["n"] == 1:
                yield {"tool": {"id": "r1", "name": "note_read",
                                "arguments": {"path": "Agent.md"}}}
            elif calls["n"] == 2:
                yield {"tool": {"id": "w1", "name": "note_write",
                                "arguments": {"path": "Agent.md", "content": "# A\n\nv2\n",
                                              "expected_version": 1,
                                              "expected_hash": h1}}}
            else:
                yield {"text": "done"}

        monkeypatch.setattr(providers, "complete", scripted2)
        _mkconv("tconv2")
        res = await executions.submit("tconv2", "update Agent.md to v2", "stub/m")
        for _ in range(200):
            if executions.get_snapshot(res["execution_id"])["status"] in events.TERMINAL:
                break
            await asyncio.sleep(0.1)
        assert executions.get_snapshot(res["execution_id"])["status"] == "completed"
        got = client.get("/api/v1/notes", params={"path": "Agent.md"}).json()
        assert got["version"] == 2 and "v2" in got["content"]
        # stale write with the old version must conflict
        from app.services import vault as _v
        try:
            _v.update_note("Agent.md", "stale", actor="ai",
                           expected_version=1, expected_hash=h1)
            raise SystemExit("stale write should have conflicted")
        except _v.NoteConflict as exc:
            assert "version 2" in str(exc)

    asyncio.run(main())
