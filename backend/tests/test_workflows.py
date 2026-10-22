"""Approval + pause/resume workflows with a stubbed provider.

Covers: waiting_approval → approve → completed with real shell output;
pause during run → resume → completed. All routing/persistence is real.
"""
import asyncio
import json

from app import db, events
from app.services import agent, executions, model_catalog, providers


class StubState:
    shell_calls = 0


async def shell_then_text(model, messages):
    if StubState.shell_calls == 0:
        StubState.shell_calls += 1
        yield {"tool": {"id": "s1", "name": "shell_exec",
                        "arguments": {"command": "echo APPROVED-RUN"}}}
    yield {"text": "shell was approved and ran"}


async def slow_text(model, messages):
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
