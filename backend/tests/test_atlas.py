import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import db, events
from app.services import executions, model_catalog, workspace as ws


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
    db.reset_for_tests(tmp_path)
    yield tmp_path


@pytest.fixture()
def client(tmp_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_state_machine_transitions(tmp_db):
    assert events.valid_transition("queued", "starting")
    assert events.valid_transition("running", "waiting_input")
    assert events.valid_transition("waiting_input", "running")
    assert events.valid_transition("running", "paused")
    assert events.valid_transition("paused", "running")
    assert events.valid_transition("running", "completed") is False  # must verify first
    assert events.valid_transition("verifying", "completed")
    assert events.valid_transition("completed", "running") is False
    assert events.valid_transition("failed", "failed")


def test_text_tool_call_fallback_parsing(tmp_db):
    from app.services.agent import extract_text_tool_calls
    text = 'Sure.\n```json\n{"name": "fs_write", "arguments": {"path": "a.txt", "content": "hi"}}\n```'
    calls = extract_text_tool_calls(text)
    assert len(calls) == 1 and calls[0]["name"] == "fs_write"
    assert calls[0]["arguments"]["path"] == "a.txt"
    assert extract_text_tool_calls("no tools here") == []
    assert extract_text_tool_calls('```json\n{"name": "evil_tool"}\n```') == []
    multi = '```json\n{"name": "fs_write", "arguments": {"path": "a"}}\n{"name": "shell_exec", "arguments": {"command": "ls"}}\n```'
    assert [c["name"] for c in extract_text_tool_calls(multi)] == ["fs_write", "shell_exec"]


def test_catalog_caches_live_view(tmp_db, monkeypatch):
    """Refresh must persist the live Go catalog (chat models only, no ollama)."""
    import asyncio
    from app.services import model_catalog

    async def fake_live():
        return [{"id": "opencode-go/m1", "name": "M1", "provider": "go",
                 "protocol": "chat_completions", "endpoint": "http://x",
                 "context_window": None, "supports_tools": True}]

    monkeypatch.setattr(model_catalog, "_fetch_live", fake_live)
    monkeypatch.setattr(model_catalog, "key_configured", lambda: True)

    async def go():
        first = await model_catalog.get_catalog(force_refresh=True)
        assert first["source"] == "live"
        second = await model_catalog.get_catalog()  # cached read
        ids = [m["id"] for m in second["models"]]
        assert ids == ["opencode-go/m1"]
        assert not any(i.startswith("ollama/") for i in ids)
        ok, _ = await model_catalog.validate_model("opencode-go/m1")
        assert ok
        nok, _ = await model_catalog.validate_model("ollama/qwen3:0.6b")
        assert not nok

    asyncio.run(go())


def test_go_session_headers(tmp_db):
    from app.services.providers import _headers
    h = _headers(session_id="conv_abc")
    assert h["x-opencode-session"] == "conv_abc"
    assert h["User-Agent"] == "atlas/1.0"
    assert "Authorization" in h  # Hermes-bridged key present in this env
    h2 = _headers()
    assert "x-opencode-session" not in h2


def test_go_routing_table(tmp_db):
    from app.services.model_catalog import _protocol_for
    assert _protocol_for("opencode-go/muse-spark-1.3-contributor", {})[0] == "responses"
    assert _protocol_for("opencode-go/grok-4.7", {})[0] == "responses"
    assert _protocol_for("opencode-go/gpt-5.6-luna", {})[0] == "responses"
    ep = _protocol_for("opencode-go/muse-spark-1.3-contributor", {})
    assert ep[1] == "https://opencode.ai/inference/go/openai/v1/responses"
    assert _protocol_for("opencode-go/claude-haiku-5-5", {}) == (
        "messages", "https://opencode.ai/inference/go/anthropic/v1/messages")
    assert _protocol_for("opencode-go/minimax-m3", {})[0] == "messages"
    assert _protocol_for("opencode-go/minimax-m3.1-flash", {})[0] == "chat_completions"
    assert _protocol_for("opencode-go/gemini-4-argon-contributor", {}) == (
        "google", "https://opencode.ai/inference/go/google/v1beta/models/gemini-4-argon-contributor")
    assert _protocol_for("opencode-go/glm-5.3-flash", {}) == (
        "chat_completions", "https://opencode.ai/inference/go/openai/v1/chat/completions")
    assert _protocol_for("opencode-go/beam-free", {})[0] == "chat_completions"


def test_terminal_states_lock(tmp_db):
    cid = "conv_state"
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
               " VALUES (?, 't', 'm', ?, ?, ?)", (cid, cid, now, now))
    eid = db.new_id("exe")
    db.execute("INSERT INTO executions (id, conversation_id, status, model, workspace_id,"
               " input_text, current_step, max_steps, pending_json, error, started_at, updated_at, ended_at)"
               " VALUES (?, ?, 'completed', 'm', ?, '', 0, 5, '{}', '', ?, ?, ?)",
               (eid, cid, cid, now, now, now))
    assert events.set_execution_status(eid, "running") is False


def test_event_replay_ordering_and_dedup(tmp_db):
    eid = "exe_replay"
    events.append(eid, "turn_queued", {"a": 1})
    events.append(eid, "text_delta", {"delta": "hi"})
    events.append(eid, "turn_completed", {})
    all_ev = events.replay(eid, 0)
    assert [e["seq"] for e in all_ev] == [1, 2, 3]
    tail = events.replay(eid, 2)
    assert [e["seq"] for e in tail] == [3]
    assert events.replay("missing", 0) == []


def test_catalog_normalization_never_invents_ids(tmp_db):
    raw = [
        {"id": "qwen-3-coder-480b", "name": "Qwen 3 Coder", "provider": "opencode-go"},
        {"id": "anthropic/claude-x", "protocol": "messages"},
        {"name": "no-id"},
        "garbage",
    ]
    models = model_catalog.normalize([m for m in raw if isinstance(m, dict)])
    ids = [m["id"] for m in models]
    assert "opencode-go/qwen-3-coder-480b" in ids
    assert "anthropic/claude-x" in ids
    assert all("/" in i or i.startswith("opencode-go/") for i in ids)
    claude = next(m for m in models if "claude" in m["id"])
    assert claude["protocol"] == "messages"
    assert claude["endpoint"].endswith("/messages")


def test_workspace_path_traversal_blocked(tmp_db):
    import asyncio
    from pathlib import Path
    root = Path(tmp_db) / "ws"
    root.mkdir()
    with pytest.raises(PermissionError):
        ws.resolve(root, "../../etc/passwd")
    with pytest.raises(PermissionError):
        ws.resolve(root, "/absolute/evil")


def test_tool_lifecycle_write_read_edit(tmp_db):
    from pathlib import Path
    root = Path(tmp_db) / "ws2"
    root.mkdir()
    w = asyncio.run(ws.run_tool("fs_write", {"path": "a.txt", "content": "hello"}, root))
    assert w["ok"] and "diff" in w
    r = asyncio.run(ws.run_tool("fs_read", {"path": "a.txt"}, root))
    assert r["ok"] and r["output"] == "hello"
    e = asyncio.run(ws.run_tool("fs_edit", {"path": "a.txt", "old_string": "hello",
                                            "new_string": "world"}, root))
    assert e["ok"]
    bad = asyncio.run(ws.run_tool("fs_edit", {"path": "a.txt", "old_string": "zzz",
                                              "new_string": "q"}, root))
    assert not bad["ok"]


def test_permissions_policy(tmp_db):
    need, _ = ws.needs_approval("fs_read", {"path": "x"}, auto_approve=True)
    assert need is False
    need, reason = ws.needs_approval("shell_exec", {"command": "rm -rf /"}, auto_approve=True)
    assert need is True and reason
    need, _ = ws.needs_approval("shell_exec", {"command": "echo hi"}, auto_approve=False)
    assert need is True
    need, _ = ws.needs_approval("shell_exec", {"command": "echo hi"}, auto_approve=True)
    assert need is False
    need, _ = ws.needs_approval("mystery_tool", {}, auto_approve=True)
    assert need is True


def test_message_persistence_and_isolation(client):
    c1 = client.post("/api/v1/conversations", json={}).json()
    c2 = client.post("/api/v1/conversations", json={}).json()
    assert c1["id"] != c2["id"]
    # send is rejected without a valid model, but conversation + validation plumbing works
    r = client.post(f"/api/v1/conversations/{c1['id']}/messages",
                    json={"content": "hi", "model": "nope/missing"})
    assert r.status_code == 400
    msgs = client.get(f"/api/v1/conversations/{c2['id']}/messages").json()["messages"]
    assert msgs == []


def test_conversation_crud_and_search(client):
    c = client.post("/api/v1/conversations", json={"title": "Atlas Test"}).json()
    got = client.get(f"/api/v1/conversations/{c['id']}").json()
    assert got["title"] == "Atlas Test"
    patched = client.patch(f"/api/v1/conversations/{c['id']}",
                           json={"model": "ollama/test"}).json()
    assert patched["model"] == "ollama/test"
    found = client.get("/api/v1/conversations", params={"q": "Atlas Test"}).json()
    assert any(x["id"] == c["id"] for x in found)
    assert client.delete(f"/api/v1/conversations/{c['id']}").json()["ok"] is True
    assert client.get(f"/api/v1/conversations/{c['id']}").status_code == 404


def test_recovery_marks_orphans_interrupted(tmp_db):
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
               " VALUES ('c1', 't', 'm', 'c1', ?, ?)", (now, now))
    db.execute("INSERT INTO executions (id, conversation_id, status, model, workspace_id,"
               " input_text, current_step, max_steps, pending_json, error, started_at, updated_at, ended_at)"
               " VALUES ('e1', 'c1', 'running', 'm', 'c1', '', 0, 5, '{}', '', ?, ?, 0)", (now, now))
    assert executions.recover_on_startup() == 1
    assert executions.get_snapshot("e1")["status"] == "interrupted"


def test_retry_only_terminal(tmp_db):
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
               " VALUES ('c9', 't', 'm', 'c9', ?, ?)", (now, now))
    db.execute("INSERT INTO executions (id, conversation_id, status, model, workspace_id,"
               " input_text, current_step, max_steps, pending_json, error, started_at, updated_at, ended_at)"
               " VALUES ('e9', 'c9', 'running', 'm', 'c9', 'hi', 0, 5, '{}', '', ?, ?, 0)", (now, now))
    assert asyncio.run(executions.retry_execution("e9")) is False
