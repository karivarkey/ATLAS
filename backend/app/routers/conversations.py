from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..schemas import (
    AnswerInput, ApprovalResolve, ConversationCreate, ConversationPatch,
    MessageSend, RetryInput,
)
from ..services import executions, model_catalog
from .. import config

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


def _conv_row(r) -> dict:
    active = executions.active_for_conversation(r["id"])
    return {"id": r["id"], "title": r["title"], "model": r["model"] or config.DEFAULT_MODEL,
            "workspace_id": r["workspace_id"], "created_at": r["created_at"],
            "updated_at": r["updated_at"], "active_execution": active}


@router.get("")
def list_conversations(q: str = Query(default="")):
    if q:
        rows = db.query(
            "SELECT * FROM conversations WHERE title LIKE ? ORDER BY updated_at DESC LIMIT 100",
            (f"%{q}%",),
        )
    else:
        rows = db.query("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 100")
    return [_conv_row(r) for r in rows]


@router.post("", status_code=201)
def create_conversation(body: ConversationCreate):
    cid = db.new_id("conv")
    now = db.now_ms()
    db.execute(
        "INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (cid, body.title or "New conversation", body.model or config.DEFAULT_MODEL,
         body.workspace_id or cid, now, now),
    )
    row = db.query_one("SELECT * FROM conversations WHERE id = ?", (cid,))
    assert row is not None
    return _conv_row(row)


@router.get("/{cid}")
def get_conversation(cid: str):
    row = db.query_one("SELECT * FROM conversations WHERE id = ?", (cid,))
    if not row:
        raise HTTPException(404, "conversation not found")
    return _conv_row(row)


@router.patch("/{cid}")
def patch_conversation(cid: str, body: ConversationPatch):
    row = db.query_one("SELECT * FROM conversations WHERE id = ?", (cid,))
    if not row:
        raise HTTPException(404, "conversation not found")
    if body.title is not None:
        db.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                   (body.title[:200], db.now_ms(), cid))
    if body.model is not None:
        db.execute("UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
                   (body.model, db.now_ms(), cid))
    return get_conversation(cid)


@router.delete("/{cid}")
def delete_conversation(cid: str):
    db.execute("DELETE FROM conversations WHERE id = ?", (cid,))
    db.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
    return {"ok": True}


@router.get("/{cid}/messages")
def list_messages(cid: str, limit: int = Query(default=200, le=1000),
                  before: int = Query(default=0)):
    if not db.query_one("SELECT id FROM conversations WHERE id = ?", (cid,)):
        raise HTTPException(404, "conversation not found")
    if before:
        rows = db.query(
            "SELECT * FROM messages WHERE conversation_id = ? AND created_at < ?"
            " ORDER BY created_at DESC LIMIT ?",
            (cid, before, limit),
        )
    else:
        rows = db.query(
            "SELECT * FROM messages WHERE conversation_id = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (cid, limit),
        )
    items = [
        {"id": r["id"], "role": r["role"], "content": r["content"], "model": r["model"],
         "execution_id": r["execution_id"], "status": r["status"], "created_at": r["created_at"]}
        for r in reversed(rows)
    ]
    return {"messages": items}


@router.post("/{cid}/messages", status_code=202)
async def send_message(cid: str, body: MessageSend):
    if not db.query_one("SELECT id FROM conversations WHERE id = ?", (cid,)):
        raise HTTPException(404, "conversation not found")
    if executions.active_for_conversation(cid):
        raise HTTPException(409, "an execution is already running in this conversation")
    model = body.model or (db.query_one(
        "SELECT model FROM conversations WHERE id = ?", (cid,)) or {"model": ""})["model"]
    ok, _ = await model_catalog.validate_model(model or config.DEFAULT_MODEL)
    if not ok:
        catalog = await model_catalog.get_catalog()
        raise HTTPException(
            400,
            {"error": f"model '{model}' is not available",
             "available": [m["id"] for m in catalog["models"][:50]]},
        )
    try:
        result = await executions.submit(cid, body.content, model or config.DEFAULT_MODEL)
    except KeyError:
        raise HTTPException(404, "conversation not found")
    return result


@router.get("/{cid}/status")
def conversation_status(cid: str):
    active = executions.active_for_conversation(cid)
    return {"active_execution": active}


@router.post("/{cid}/answer")
async def answer_question(cid: str, body: AnswerInput):
    active = executions.active_for_conversation(cid)
    if not active:
        raise HTTPException(404, "no active execution")
    if not await executions.answer(active["id"], body.text):
        raise HTTPException(409, "execution is not waiting for input")
    return {"ok": True}


@router.post("/{cid}/approve")
async def approve_tool(cid: str, body: ApprovalResolve):
    active = executions.active_for_conversation(cid)
    if not active:
        raise HTTPException(404, "no active execution")
    if not await executions.resolve_approval(active["id"], body.approved):
        raise HTTPException(409, "execution is not waiting for approval")
    return {"ok": True}
