from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import db, events
from ..schemas import RetryInput
from ..services import executions, model_catalog

router = APIRouter(prefix="/api/v1", tags=["executions"])


@router.get("/models")
async def list_models(refresh: bool = False):
    catalog = await model_catalog.get_catalog(force_refresh=refresh)
    return catalog


@router.post("/models/refresh")
async def refresh_models():
    return await model_catalog.get_catalog(force_refresh=True)


@router.get("/config")
def get_config():
    from .. import config
    return {
        "default_model": config.DEFAULT_MODEL,
        "key_configured": bool(config.OPENCODE_ZEN_API_KEY),
        "ollama_base": config.OLLAMA_BASE,
        "max_steps": config.MAX_STEPS_PER_EXECUTION,
        "auto_approve": config.AUTO_APPROVE_WORKSPACE_WRITES,
    }


@router.get("/executions/{eid}")
def get_execution(eid: str):
    snap = executions.get_snapshot(eid)
    if not snap:
        raise HTTPException(404, "execution not found")
    return snap


@router.get("/executions/{eid}/events")
def execution_events(eid: str, from_seq: int = 0):
    snap = executions.get_snapshot(eid)
    if not snap:
        raise HTTPException(404, "execution not found")
    return {"events": events.replay(eid, from_seq)}


@router.get("/executions/{eid}/stream")
async def execution_stream(eid: str, from_seq: int = 0):
    snap = executions.get_snapshot(eid)
    if not snap:
        raise HTTPException(404, "execution not found")

    async def gen():
        yield ": connected\n\n"
        for ev in events.replay(eid, from_seq):
            yield f"id: {ev['seq']}\ndata: {json.dumps(ev)}\n\n"
        last = from_seq
        rows = events.replay(eid, from_seq)
        if rows:
            last = rows[-1]["seq"]
        q = await events.subscribe(eid)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if ev["seq"] <= last:
                    continue
                last = ev["seq"]
                yield f"id: {ev['seq']}\ndata: {json.dumps(ev)}\n\n"
                if ev["type"] in ("turn_completed", "turn_failed", "cancelled"):
                    # Give the client a moment, keep stream open briefly for replay safety.
                    await asyncio.sleep(0.5)
                    break
        finally:
            await events.unsubscribe(eid, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/executions/{eid}/stop")
async def stop_execution(eid: str):
    if not executions.get_snapshot(eid):
        raise HTTPException(404, "execution not found")
    return {"ok": await executions.stop(eid)}


@router.post("/executions/{eid}/pause")
async def pause_execution(eid: str):
    if not await executions.pause(eid):
        raise HTTPException(409, "execution cannot be paused in its current state")
    return {"ok": True}


@router.post("/executions/{eid}/resume")
async def resume_execution(eid: str):
    if not await executions.resume(eid):
        raise HTTPException(409, "execution cannot be resumed in its current state")
    return {"ok": True}


@router.post("/executions/{eid}/answer")
async def answer_execution(eid: str, body: dict):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if not await executions.answer(eid, text):
        raise HTTPException(409, "execution is not waiting for input")
    return {"ok": True}


@router.post("/executions/{eid}/approve")
async def approve_execution(eid: str, body: dict):
    if not await executions.resolve_approval(eid, bool(body.get("approved"))):
        raise HTTPException(409, "execution is not waiting for approval")
    return {"ok": True}


@router.post("/executions/{eid}/retry")
async def retry_execution(eid: str, body: RetryInput):
    if not await executions.retry_execution(eid, body.model):
        raise HTTPException(409, "execution cannot be retried in its current state")
    return {"ok": True}


@router.get("/conversations/{cid}/artifacts")
def list_artifacts(cid: str):
    rows = db.query(
        "SELECT * FROM artifacts WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 100",
        (cid,),
    )
    return [{"id": r["id"], "execution_id": r["execution_id"], "path": r["path"],
             "kind": r["kind"], "summary": r["summary"], "created_at": r["created_at"]}
            for r in rows]
