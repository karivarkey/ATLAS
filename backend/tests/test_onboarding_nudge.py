import asyncio

from app import db
from app import events as ev
from app.services import executions, model_catalog, onboarding, providers
from app.services import embeddings as _emb


def _setup2(tmp_path, monkeypatch, llm):
    db.reset_for_tests(tmp_path)

    async def _ok(m):
        return True, {"id": m, "protocol": "chat_completions",
                      "endpoint": "http://x", "supports_tools": True}

    async def _fake(texts):
        return [[1.0] * 8 for _ in texts]

    monkeypatch.setattr(model_catalog, "validate_model", _ok)
    monkeypatch.setattr(providers, "complete", llm)
    monkeypatch.setattr(_emb, "embed_batch", _fake)


def test_final_turn_nudge_reaches_model(tmp_path, monkeypatch):
    """After 6+ user turns the wrap-up nudge is injected into the model input."""
    seen = {}

    async def spy(model, messages, session_id=""):
        seen["n"] = len(messages)
        seen["has_nudge"] = any(
            "FINAL TURNS" in (m.get("content") or "") for m in messages)
        yield {"text": "What else should I know?"}

    _setup2(tmp_path, monkeypatch, spy)
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, scope_id, memory_excluded,"
               " workspace_id, created_at, updated_at)"
               " VALUES ('obn', 'Getting to know you', 'stub/m', 'user:default', 0, 'obn', ?, ?)",
               (now, now))
    for i in range(7):
        mid = db.new_id("msg")
        db.execute("INSERT INTO messages (id, conversation_id, role, content, model, parts_json,"
                   " execution_id, status, created_at) VALUES (?, 'obn', 'user', ?, '', '[]', '', 'done', ?)",
                   (mid, f"answer {i}", now + i))

    async def main():
        res = await executions.submit("obn", "one more", "stub/m",
                                      system_prompt=onboarding.ONBOARD_PROMPT)
        eid = res["execution_id"]
        for _ in range(200):
            if executions.get_snapshot(eid)["status"] in ev.TERMINAL:
                break
            await asyncio.sleep(0.1)

    asyncio.run(main())
    assert seen.get("has_nudge") is True, seen


def test_max_turns_caps_interview(tmp_path, monkeypatch):
    """After ~10 user turns the interview auto-completes instead of looping."""
    from app.services import onboarding as _ob

    async def quiet(model, messages, session_id=""):
        yield {"text": "Tell me more."}

    _setup2(tmp_path, monkeypatch, quiet)
    assert _ob.is_onboarded() is False
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, scope_id, memory_excluded,"
               " workspace_id, created_at, updated_at)"
               " VALUES ('obc', 'Getting to know you', 'stub/m', 'user:default', 0, 'obc', ?, ?)",
               (now, now))
    for i in range(9):
        mid = db.new_id("msg")
        db.execute("INSERT INTO messages (id, conversation_id, role, content, model, parts_json,"
                   " execution_id, status, created_at) VALUES (?, 'obc', 'user', ?, '', '[]', '', 'done', ?)",
                   (mid, f"answer {i}", now + i))

    async def main():
        res = await executions.submit("obc", "last one", "stub/m",
                                      system_prompt=onboarding.ONBOARD_PROMPT)
        eid = res["execution_id"]
        for _ in range(200):
            if executions.get_snapshot(eid)["status"] in ev.TERMINAL:
                break
            await asyncio.sleep(0.1)

    asyncio.run(main())
    assert _ob.is_onboarded() is True
