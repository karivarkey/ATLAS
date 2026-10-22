from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import config, db
from ..services import executions, onboarding

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


@router.get("/status")
def status():
    from .. import config
    return {"onboarded": onboarding.is_onboarded(),
            "key_configured": bool(config.OPENCODE_ZEN_API_KEY),
            "default_model": config.DEFAULT_MODEL}


@router.post("/start")
async def start(body: dict | None = None):
    """Create the interview conversation and kick off the LLM greeting."""
    from ..services import model_catalog
    model = (body or {}).get("model", "") or config.DEFAULT_MODEL
    ok, _ = await model_catalog.validate_model(model)
    if not ok:
        catalog = await model_catalog.get_catalog()
        raise HTTPException(400, {"error": f"model '{model}' unavailable",
                                  "available": [m["id"] for m in catalog["models"][:20]]})
    cid = db.new_id("conv")
    now = db.now_ms()
    db.execute("INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
               " VALUES (?, 'Getting to know you', ?, ?, ?, ?)",
               (cid, model, cid, now, now))
    res = await executions.submit(cid, "Hi! Let's set up.", model,
                                  system_prompt=onboarding.ONBOARD_PROMPT)
    return {"conversation_id": cid, "execution_id": res["execution_id"]}


@router.post("/skip")
def skip():
    onboarding.set_onboarded(True, how="skipped")
    return {"ok": True}


@router.post("/reopen")
def reopen():
    onboarding.set_onboarded(False, how="reopened")
    return {"ok": True}


@router.post("/finish")
def finish(body: dict | None = None):
    """Manual finish: use the completion fence if the model already emitted it,
    otherwise just mark onboarded without a profile."""
    cid = (body or {}).get("conversation_id", "")
    if cid:
        row = db.query_one("SELECT content FROM messages WHERE conversation_id = ?"
                           " AND role = 'assistant' ORDER BY created_at DESC LIMIT 1", (cid,))
        if row and onboarding.maybe_finish_from_text(row["content"] or ""):
            return {"ok": True, "profile": True}
    onboarding.set_onboarded(True, how="manual-finish")
    return {"ok": True, "profile": False}
