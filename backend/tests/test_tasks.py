"""Tasks acceptance: CRUD/transitions, note links + rename stability,
recurrence idempotency, reminders (snooze/dismiss/quiet hours),
AI-execution attempt rules, notification dedupe."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import db, events
from app.services import executions, model_catalog, providers
from app.services import tasks as t


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


async def _wait_terminal(eid, timeout_s=60):
    for _ in range(int(timeout_s * 10)):
        if executions.get_snapshot(eid)["status"] in events.TERMINAL:
            return executions.get_snapshot(eid)["status"]
        await asyncio.sleep(0.1)
    return executions.get_snapshot(eid)["status"]


def test_crud_status_priority_deadlines(client):
    r = client.post("/api/v1/tasks", json={"title": "Write report", "priority": "high",
                                            "due_at": db.now_ms() + 3600000,
                                            "project": "alpha"}).json()
    tid = r["id"]
    assert r["status"] == "inbox"
    bad = client.post("/api/v1/tasks", json={"title": "x", "priority": "urgent"})
    assert bad.status_code == 400
    # status may only move via transition
    p = client.patch(f"/api/v1/tasks/{tid}", json={"priority": "critical"})
    assert p.status_code == 200 and p.json()["priority"] == "critical"
    ill = client.post(f"/api/v1/tasks/{tid}/transition", json={"to": "completed"})
    assert ill.status_code == 400, "inbox → completed must be rejected"
    ok = client.post(f"/api/v1/tasks/{tid}/transition", json={"to": "planned"}).json()
    assert ok["status"] == "planned"
    ok = client.post(f"/api/v1/tasks/{tid}/transition", json={"to": "in_progress"}).json()
    assert ok["status"] == "in_progress"
    hist = client.get(f"/api/v1/tasks/{tid}/history").json()["history"]
    actions = [h["action"] for h in hist]
    assert actions[0] == "created" and actions.count("transition") == 2
    assert "updated" in actions  # the priority patch


def test_note_linking_survives_rename(client):
    n = client.post("/api/v1/notes", json={"path": "Spec.md", "content": "# Spec"}).json()
    task = client.post("/api/v1/tasks", json={"title": "Implement spec"}).json()
    linked = client.post(f"/api/v1/tasks/{task['id']}/notes",
                         json={"note_path": "Spec.md"}).json()
    assert linked["notes"][0]["path"] == "Spec.md"
    client.post("/api/v1/notes/move",
                json={"old_path": "Spec.md", "new_path": "docs/Spec.md"})
    got = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert got["notes"][0]["path"] == "docs/Spec.md", "link must follow the rename via stable id"
    assert got["notes"][0]["note_id"] == n["id"]


def test_recurrence_idempotent_across_restart(client):
    task = client.post("/api/v1/tasks",
                       json={"title": "Standup", "recurrence": "weekdays"}).json()
    g1 = client.post(f"/api/v1/tasks/{task['id']}/occurrences/generate",
                     json={"horizon_days": 14}).json()
    assert g1["new"] > 0
    total = g1["total"]
    # simulate restart / double-run: no duplicates
    g2 = client.post(f"/api/v1/tasks/{task['id']}/occurrences/generate",
                     json={"horizon_days": 14}).json()
    assert g2["new"] == 0 and g2["total"] == total
    occ = client.get(f"/api/v1/tasks/{task['id']}/occurrences").json()["occurrences"]
    assert len(occ) == total
    keys = [o["occurrence_key"] for o in occ]
    assert len(set(keys)) == len(keys)
    import datetime as d
    for o in occ:
        assert d.date.fromisoformat(o["occurrence_key"]).weekday() < 5


def test_reminders_snooze_dismiss_quiet_hours(client, monkeypatch):
    past = db.now_ms() - 7200000
    task = client.post("/api/v1/tasks",
                       json={"title": "Pay bill", "priority": "critical",
                             "due_at": past}).json()
    # force business hours for determinism
    monkeypatch.setattr(t, "in_quiet_hours", lambda *a, **k: False)
    n1 = client.post("/api/v1/reminders/evaluate", json={}).json()["notified"]
    assert len(n1) == 1 and n1[0]["task_id"] == task["id"]
    # no infinite nagging: second evaluate creates nothing (unread exists)
    n2 = client.post("/api/v1/reminders/evaluate", json={}).json()["notified"]
    assert n2 == []
    assert client.get("/api/v1/notifications/unread-count").json()["unread"] == 1
    # snooze the underlying reminder → silenced even after clearing the unread
    rem = client.post("/api/v1/reminders",
                      json={"task_id": task["id"], "fire_at": past}).json()
    client.post(f"/api/v1/reminders/{rem['id']}/snooze",
                json={"until": db.now_ms() + 3600000})
    for n in client.get("/api/v1/notifications").json()["notifications"]:
        client.post(f"/api/v1/notifications/{n['id']}/dismiss")
    n3 = client.post("/api/v1/reminders/evaluate", json={}).json()["notified"]
    assert n3 == [], "snoozed tasks must not notify"
    # dismiss → also silenced
    client.post(f"/api/v1/reminders/{rem['id']}/dismiss")
    assert client.post("/api/v1/reminders/evaluate", json={}).json()["notified"] == []
    # quiet hours → score 0
    monkeypatch.setattr(t, "in_quiet_hours", lambda *a, **k: True)
    assert t.reminder_score({**task, "status": "planned"}, db.now_ms()) == 0.0


def test_notification_dedupe(client):
    a = client.post("/api/v1/reminders", json={}).status_code  # missing task → 404/400
    assert a in (400, 404)
    n1 = t.notify("reminder", "Hi", "body", task_id="x1", dedupe_key="k-same")
    n2 = t.notify("reminder", "Hi", "body", task_id="x1", dedupe_key="k-same")
    assert n1["created"] is True and n2["created"] is False
    assert n1["notification"]["id"] == n2["notification"]["id"]
    assert client.get("/api/v1/notifications/unread-count").json()["unread"] == 1


def test_reminders_evaluate_and_dedupe(tmp_path):
    """Engine: overdue high-priority task notifies once per day; dismiss suppresses."""
    import datetime as _dt
    from app import db as _db

    _db.reset_for_tests(tmp_path)
    noon = int(_dt.datetime(2026, 10, 22, 12, 0).timestamp() * 1000)
    assert not t.in_quiet_hours(noon)
    task = t.create_task("Overdue unit", priority="high",
                         due_at=noon - 25 * 3600 * 1000)
    assert t.reminder_score(t.get_task(task["id"]), noon) > 2.0
    made = t.evaluate_reminders(now_ms=noon)
    assert len(made) == 1 and made[0]["task_id"] == task["id"]
    assert t.evaluate_reminders(now_ms=noon + 1000) == []  # same-day dedupe
    t.mark_notification(made[0]["id"], "dismissed")
    assert t.evaluate_reminders(now_ms=noon + 2000) == []


def test_ai_execution_attempt_recorded_failure_does_not_complete(client, tmp_env, monkeypatch):
    async def main():
        async def text_only(model, messages, session_id=""):
            yield {"text": "I thought about it (no tools used)"}

        monkeypatch.setattr(providers, "complete", text_only)
        _mkconv("tconv")
        task = client.post("/api/v1/tasks", json={"title": "Do thing"}).json()
        ex = client.post(f"/api/v1/tasks/{task['id']}/execute",
                         json={"conversation_id": "tconv"}).json()
        assert "execution_id" in ex
        st = await _wait_terminal(ex["execution_id"])
        assert st == "completed", "stub run should finish"
        # unverified criteria → task must NOT complete
        v = client.post(f"/api/v1/tasks/{task['id']}/verify",
                        json={"execution_id": ex["execution_id"],
                              "criteria_met": False, "evidence": ""}).json()
        assert v["verified"] is False
        assert v["task"]["status"] != "completed"
        # criteria claimed but execution failed → still must NOT complete
        async def boom(model, messages, session_id=""):
            raise RuntimeError("provider down")
            yield  # unreachable; makes this an async generator

        monkeypatch.setattr(providers, "complete", boom)
        task2 = client.post("/api/v1/tasks", json={"title": "Do other"}).json()
        _mkconv("tconv2")
        ex2 = client.post(f"/api/v1/tasks/{task2['id']}/execute",
                          json={"conversation_id": "tconv2"}).json()
        st2 = await _wait_terminal(ex2["execution_id"])
        assert st2 == "failed"
        v2 = client.post(f"/api/v1/tasks/{task2['id']}/verify",
                         json={"execution_id": ex2["execution_id"],
                               "criteria_met": True,
                               "evidence": "it worked, trust me"}).json()
        assert v2["verified"] is False, "failed execution can never verify"
        assert v2["task"]["status"] != "completed"
        atts = client.get(f"/api/v1/tasks/{task['id']}/attempts").json()["attempts"]
        assert len(atts) == 1 and atts[0]["status"] == "unverified"

    asyncio.run(main())
