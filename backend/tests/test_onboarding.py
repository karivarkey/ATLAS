"""Onboarding: fence parsing, profile application, execution-level finish."""
import asyncio
import json

from app import db
from app import events as ev
from app.services import executions, model_catalog, onboarding, providers


async def stub_embed(texts):
    return [[1.0] * 8 for _ in texts]


PROFILE_FENCE = ('Thanks! One last thing — what should I call you? Actually I have enough. '
                 'Welcome aboard!\n```onboarding_complete\n'
                 '{"name": "Test User", "role": "tester", "projects": ["Atlas"],'
                 ' "stack": ["python"], "preferences": {"answer_style": "short"},'
                 ' "constraints": [], "notes": "loves testing"}\n```')


async def fake_finisher(model, messages, session_id=""):
    yield {"text": PROFILE_FENCE}


async def fake_chat(model, messages, session_id=""):
    yield {"text": "What is your name?"}


async def _ok(m):
    return True, {"id": m, "protocol": "chat_completions",
                  "endpoint": "http://x", "supports_tools": True}


def _setup(tmp_path, monkeypatch, llm=None):
    from app.services import embeddings
    db.reset_for_tests(tmp_path)
    monkeypatch.setattr(model_catalog, "validate_model", _ok)
    monkeypatch.setattr(providers, "complete", llm or fake_chat)
    monkeypatch.setattr(embeddings, "embed_batch", stub_embed)


def test_parse_and_apply(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert onboarding.parse_completion("no fence here") is None
    assert onboarding.parse_completion("```onboarding_complete\n{bad}\n```") is None
    p = onboarding.parse_completion(PROFILE_FENCE)
    assert p and p["name"] == "Test User"
    v = onboarding.apply_profile(p)
    assert v >= 1
    from app.services import memory_files as mf
    assert "Test User" in mf.read_file("USER.md")
    recs = db.query("SELECT * FROM memory_records WHERE memory_type='preference'")
    assert recs and "Test User" in recs[0]["summary"]
    assert onboarding.is_onboarded() is False  # apply alone doesn't flip the flag
    assert onboarding.maybe_finish_from_text(PROFILE_FENCE)["name"] == "Test User"
    assert onboarding.is_onboarded() is True


def test_execution_finish_marks_onboarded(tmp_path, monkeypatch):
    from app import events as _ev
    _setup(tmp_path, monkeypatch, llm=fake_finisher)
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, scope_id, memory_excluded,"
               " workspace_id, created_at, updated_at)"
               " VALUES ('ob1', 'Getting to know you', 'stub/m', 'user:default', 0, 'ob1', ?, ?)",
               (now, now))

    async def main():
        res = await executions.submit("ob1", "Hi! Let's set up.", "stub/m",
                                      system_prompt=onboarding.ONBOARD_PROMPT)
        eid = res["execution_id"]
        for _ in range(200):
            if executions.get_snapshot(eid)["status"] in _ev.TERMINAL:
                break
            await asyncio.sleep(0.1)
        return eid

    eid = asyncio.run(main())
    assert executions.get_snapshot(eid)["status"] == "completed"
    assert onboarding.is_onboarded() is True
    from app.services import memory_files as mf
    assert "Test User" in mf.read_file("USER.md")


def test_repeating_turn_detector(tmp_path, monkeypatch):
    from app.services.agent import _repeating_turn
    _setup(tmp_path, monkeypatch)
    assert _repeating_turn("Got it, Gee. Got it, Gee. Got it, Gee.") is True
    assert _repeating_turn("Hello there, nice to meet you!") is False
    assert _repeating_turn("") is False
    assert _repeating_turn("Short. Tiny. Small.") is False


def test_repetition_breaks_loop_early(tmp_path, monkeypatch):
    """A model stuck repeating itself terminates early instead of looping."""
    from app import events as _ev
    from app.services import executions as _exec

    _setup(tmp_path, monkeypatch)

    async def loopy(model, messages, session_id=""):
        yield {"text": ("Let us proceed with the onboarding interview process "
                        "for new users today. ") * 3}

    monkeypatch.setattr(providers, "complete", loopy)
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, scope_id, memory_excluded,"
               " workspace_id, created_at, updated_at)"
               " VALUES ('rep1', 'Getting to know you', 'stub/m', 'user:default', 0, 'rep1', ?, ?)",
               (now, now))
    db.execute("INSERT INTO executions (id, conversation_id, status, model, workspace_id,"
               " input_text, current_step, max_steps, pending_json, error, started_at, updated_at,"
               " ended_at, system_prompt) VALUES ('exe_rep1', 'rep1', 'queued', 'stub/m', 'rep1',"
               " 'hi', 0, 60, '{}', '', ?, ?, 0, ?)",
               (now, now, onboarding.ONBOARD_PROMPT))

    async def main():
        await _exec._supervised_run("exe_rep1", "rep1")

    asyncio.run(main())
    snap = _exec.get_snapshot("exe_rep1")
    assert snap["status"] == "completed", snap
    assert snap["current_step"] <= 6, snap  # loop broken, not budget-exhausted


def test_skip_and_reopen(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert onboarding.is_onboarded() is False
    onboarding.set_onboarded(True, how="skipped")
    assert onboarding.is_onboarded() is True
    onboarding.set_onboarded(False, how="reopened")
    assert onboarding.is_onboarded() is False
