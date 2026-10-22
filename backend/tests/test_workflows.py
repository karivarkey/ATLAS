import pytest
"""Approval + pause/resume workflows with a stubbed provider.

Covers: waiting_approval → approve → completed with real shell output;
pause during run → resume → completed. All routing/persistence is real.
"""
import asyncio
import json

from app import db, events
from app.services import agent, executions, model_catalog, providers



@pytest.fixture(autouse=True)
def _no_real_embeds(monkeypatch):
    from app.services import embeddings as _emb

    async def _fake(texts):
        return [[1.0] * 1024 for _ in texts]

    monkeypatch.setattr(_emb, "embed_batch", _fake)

class StubState:
    shell_calls = 0


async def shell_then_text(model, messages, session_id=""):
    if StubState.shell_calls == 0:
        StubState.shell_calls += 1
        yield {"tool": {"id": "s1", "name": "shell_exec",
                        "arguments": {"command": "echo APPROVED-RUN"}}}
    yield {"text": "shell was approved and ran"}


async def slow_text(model, messages, session_id=""):
    for i in range(30):
        await asyncio.sleep(0.1)
        yield {"text": f"t{i} "}
    yield {"text": "finished"}


def _mkconv(cid):
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
               " VALUES (?, 't', 'stub/m', ?, ?, ?)", (cid, cid, now, now))
    db.execute("INSERT INTO settings (key, value_json) VALUES ('auto_approve', 'false')"
               " ON CONFLICT(key) DO UPDATE SET value_json='false'")


def _run(coro):
    return asyncio.run(coro)


def test_approval_flow(tmp_path, monkeypatch):
    db.reset_for_tests(tmp_path)

    async def main():
        async def _ok(m):
            return True, {"id": m, "protocol": "chat_completions",
                          "endpoint": "http://x", "supports_tools": True}
        monkeypatch.setattr(model_catalog, "validate_model", _ok)
        monkeypatch.setattr(providers, "complete", shell_then_text)
        _mkconv("capp")
        res = await executions.submit("capp", "run echo", "stub/m")
        eid = res["execution_id"]
        for _ in range(100):
            if executions.get_snapshot(eid)["status"] == "waiting_approval":
                break
            await asyncio.sleep(0.1)
        snap = executions.get_snapshot(eid)
        assert snap["status"] == "waiting_approval", snap
        assert snap["pending"]["tool"] == "shell_exec"
        assert await executions.resolve_approval(eid, True) is True
        for _ in range(200):
            if executions.get_snapshot(eid)["status"] in events.TERMINAL:
                break
            await asyncio.sleep(0.1)
        snap = executions.get_snapshot(eid)
        assert snap["status"] == "completed", snap
        outs = [e for e in events.replay(eid, 0) if e["type"] == "tool_completed"]
        assert any("APPROVED-RUN" in json.dumps(e) for e in outs)

    _run(main())


def test_pause_resume(tmp_path, monkeypatch):
    db.reset_for_tests(tmp_path)

    async def main():
        async def _ok(m):
            return True, {"id": m, "protocol": "chat_completions",
                          "endpoint": "http://x", "supports_tools": True}
        monkeypatch.setattr(model_catalog, "validate_model", _ok)
        monkeypatch.setattr(providers, "complete", slow_text)
        _mkconv("cpause")
        db.execute("UPDATE settings SET value_json='true' WHERE key='auto_approve'")
        res = await executions.submit("cpause", "slow", "stub/m")
        eid = res["execution_id"]
        await asyncio.sleep(0.5)
        assert await executions.pause(eid) is True
        await asyncio.sleep(0.6)
        st = executions.get_snapshot(eid)["status"]
        assert st == "paused", st
        assert await executions.resume(eid) is True
        for _ in range(200):
            if executions.get_snapshot(eid)["status"] in events.TERMINAL:
                break
            await asyncio.sleep(0.1)
        assert executions.get_snapshot(eid)["status"] == "completed"

    _run(main())


class AskState:
    asked = 0


async def ask_with_options_then_text(model, messages, session_id=""):
    if AskState.asked == 0:
        AskState.asked += 1
        yield {"tool": {"id": "q1", "name": "ask_user",
                        "arguments": {"question": "Pick one?",
                                      "options": [{"id": "a", "label": "Alpha"},
                                                  {"id": "b", "label": "Beta"}],
                                      "kind": "choice"}}}
    else:
        yield {"text": "done, thanks"}


def test_conv_scoped_answer_routes_to_waiting_execution(tmp_path, monkeypatch):
    """Conv-scoped POST /conversations/{cid}/answer resolves the waiting
    execution server-side; options/kind land in pending_json + snapshot."""
    from app.routers.conversations import answer_question
    from app.schemas import AnswerInput

    db.reset_for_tests(tmp_path)
    AskState.asked = 0

    async def main():
        async def _ok(m):
            return True, {"id": m, "protocol": "chat_completions",
                          "endpoint": "http://x", "supports_tools": True}
        monkeypatch.setattr(model_catalog, "validate_model", _ok)
        monkeypatch.setattr(providers, "complete", ask_with_options_then_text)
        _mkconv("cask")
        res = await executions.submit("cask", "choose", "stub/m")
        eid = res["execution_id"]
        for _ in range(100):
            if executions.get_snapshot(eid)["status"] == "waiting_input":
                break
            await asyncio.sleep(0.1)
        snap = executions.get_snapshot(eid)
        assert snap["status"] == "waiting_input", snap
        # options + kind visible via GET execution snapshot pending
        assert snap["pending"]["question"] == "Pick one?"
        assert [o["label"] for o in snap["pending"]["options"]] == ["Alpha", "Beta"]
        assert snap["pending"]["kind"] == "choice"
        # answer through the conversation-scoped route (no execution id needed)
        out = await answer_question("cask", AnswerInput(text="Alpha"))
        assert out == {"ok": True}
        for _ in range(200):
            if executions.get_snapshot(eid)["status"] in events.TERMINAL:
                break
            await asyncio.sleep(0.1)
        snap = executions.get_snapshot(eid)
        assert snap["status"] == "completed", snap

    _run(main())


def test_conv_scoped_approve_routes_to_waiting_execution(tmp_path, monkeypatch):
    from app.routers.conversations import approve_tool
    from app.schemas import ApprovalResolve

    db.reset_for_tests(tmp_path)
    StubState.shell_calls = 0

    async def main():
        async def _ok(m):
            return True, {"id": m, "protocol": "chat_completions",
                          "endpoint": "http://x", "supports_tools": True}
        monkeypatch.setattr(model_catalog, "validate_model", _ok)
        monkeypatch.setattr(providers, "complete", shell_then_text)
        _mkconv("capp2")
        res = await executions.submit("capp2", "run echo", "stub/m")
        eid = res["execution_id"]
        for _ in range(100):
            if executions.get_snapshot(eid)["status"] == "waiting_approval":
                break
            await asyncio.sleep(0.1)
        assert executions.get_snapshot(eid)["status"] == "waiting_approval"
        out = await approve_tool("capp2", ApprovalResolve(approved=True))
        assert out == {"ok": True}
        for _ in range(200):
            if executions.get_snapshot(eid)["status"] in events.TERMINAL:
                break
            await asyncio.sleep(0.1)
        assert executions.get_snapshot(eid)["status"] == "completed"

    _run(main())


def test_stale_and_finished_execution_answer_is_clear_error(tmp_path):
    """Answering with a stale/finished execution id is a clear 409, not a crash."""
    import pytest as _pytest
    from fastapi import HTTPException

    from app.routers.executions import answer_execution, approve_execution

    db.reset_for_tests(tmp_path)

    async def main():
        # unknown id -> 404/409 with a clear message, no traceback leak
        with _pytest.raises(HTTPException) as ei:
            await answer_execution("exe_missing", {"text": "hi"})
        assert ei.value.status_code in (404, 409)
        with _pytest.raises(HTTPException) as ai:
            await approve_execution("exe_missing", {"approved": True})
        assert ai.value.status_code in (404, 409)
        # service layer returns False (no crash) for non-waiting ids
        assert await executions.answer("exe_missing", "hi") is False
        assert await executions.resolve_approval("exe_missing", True) is False
        # finished execution: answerable no longer -> 409
        _mkconv("cdone")
        db.execute(
            "INSERT INTO executions (id, conversation_id, status, model, workspace_id,"
            " input_text, current_step, max_steps, pending_json, error, started_at,"
            " updated_at, ended_at, system_prompt)"
            " VALUES ('exe_old', 'cdone', 'completed', 'm', 'cdone', 'hi', 1, 5,"
            " '{}', '', 1, 1, 2, '')",
        )
        with _pytest.raises(HTTPException) as fi:
            await answer_execution("exe_old", {"text": "late"})
        assert fi.value.status_code == 409
        assert "waiting" in fi.value.detail

    _run(main())


def test_ask_options_validation_rejects_bad_payloads(tmp_path):
    from app.services import workspace as ws

    db.reset_for_tests(tmp_path)
    good = ws.validate_ask_user({"question": "Pick?",
                                 "options": [{"id": "a", "label": "Alpha"}],
                                 "kind": "choice"})
    assert good["options"] == [{"id": "a", "label": "Alpha"}]
    assert good["kind"] == "choice"
    # plain question without options is fine
    assert ws.validate_ask_user({"question": "Why?"})["options"] == []
    # >6 options rejected
    with pytest.raises(ValueError):
        ws.validate_ask_user({"question": "q",
                              "options": [{"id": str(i), "label": "x"} for i in range(7)]})
    # label >80 chars rejected
    with pytest.raises(ValueError):
        ws.validate_ask_user({"question": "q",
                              "options": [{"id": "a", "label": "L" * 81}]})
    # malformed entries rejected
    with pytest.raises(ValueError):
        ws.validate_ask_user({"question": "q", "options": "not-a-list-shape"})
    with pytest.raises(ValueError):
        ws.validate_ask_user({"question": "q", "options": [{"id": "", "label": ""}]})
