"""Tasks + reminders + notifications API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..services import tasks as t

router = APIRouter(prefix="/api/v1", tags=["tasks"])


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, {"error": "not_found", "detail": str(exc)})
    return HTTPException(400, {"error": "bad_request", "detail": str(exc)})


# ---------------- tasks ----------------

@router.post("/tasks", status_code=201)
def create_task(body: dict):
    try:
        return t.create_task(
            title=body.get("title", ""), description=body.get("description", ""),
            status=body.get("status", "inbox"), priority=body.get("priority", "normal"),
            due_at=int(body.get("due_at") or 0), due_tz=str(body.get("due_tz", "")),
            project=str(body.get("project", "")), source_conv=str(body.get("source_conv", "")),
            source_msg=str(body.get("source_msg", "")),
            recurrence=str(body.get("recurrence", "")),
            recur_end=int(body.get("recur_end") or 0),
            recur_max=int(body.get("recur_max") or 0),
            approval_required=bool(body.get("approval_required", False)),
            eligibility=body.get("eligibility") or {},
            actor=str(body.get("actor", "user")))
    except Exception as exc:
        raise _err(exc)


@router.get("/tasks")
def list_tasks(status: str = "", project: str = "", priority: str = "",
               bucket: str = "", limit: int = 200):
    try:
        return {"tasks": t.list_tasks(status, project, priority, bucket, limit)}
    except Exception as exc:
        raise _err(exc)


@router.get("/tasks/{tid}")
def get_task(tid: str):
    try:
        return t.get_task(tid)
    except Exception as exc:
        raise _err(exc)


@router.patch("/tasks/{tid}")
def patch_task(tid: str, body: dict):
    try:
        body = dict(body)
        body.pop("actor", None)
        body.pop("status", None)  # status moves only via /transition
        return t.update_task(tid, actor=str(body.pop("_actor", "user")), **body)
    except Exception as exc:
        raise _err(exc)


@router.post("/tasks/{tid}/transition")
def transition(tid: str, body: dict):
    try:
        return t.transition_task(tid, str(body.get("to", "")),
                                 actor=str(body.get("actor", "user")),
                                 note=str(body.get("note", "")))
    except Exception as exc:
        raise _err(exc)


@router.delete("/tasks/{tid}")
def delete_task(tid: str):
    try:
        return t.delete_task(tid)
    except Exception as exc:
        raise _err(exc)


@router.get("/tasks/{tid}/history")
def history(tid: str):
    try:
        return {"history": t.task_history(tid)}
    except Exception as exc:
        raise _err(exc)


@router.post("/tasks/{tid}/notes")
def link_note(tid: str, body: dict):
    try:
        return t.link_note(tid, str(body.get("note_path", "")),
                           actor=str(body.get("actor", "user")))
    except Exception as exc:
        raise _err(exc)


@router.delete("/tasks/{tid}/notes")
def unlink_note(tid: str, note_id: str):
    try:
        return t.unlink_note(tid, note_id)
    except Exception as exc:
        raise _err(exc)


@router.post("/tasks/{tid}/occurrences/generate")
def gen_occ(tid: str, body: dict | None = None):
    try:
        return t.generate_occurrences(tid, int((body or {}).get("horizon_days", 30)))
    except Exception as exc:
        raise _err(exc)


@router.get("/tasks/{tid}/occurrences")
def list_occ(tid: str):
    try:
        return {"occurrences": t.list_occurrences(tid)}
    except Exception as exc:
        raise _err(exc)


@router.get("/tasks/{tid}/attempts")
def attempts(tid: str):
    try:
        return {"attempts": t.list_attempts(tid)}
    except Exception as exc:
        raise _err(exc)


@router.post("/tasks/{tid}/execute")
async def execute(tid: str, body: dict | None = None):
    try:
        return await t.execute_task(tid, conversation_id=str((body or {}).get("conversation_id", "")),
                                    model=str((body or {}).get("model", "")),
                                    actor=str((body or {}).get("actor", "user")))
    except Exception as exc:
        raise _err(exc)


@router.post("/tasks/{tid}/verify")
def verify(tid: str, body: dict):
    try:
        return t.verify_task_attempt(tid, str(body.get("execution_id", "")),
                                     bool(body.get("criteria_met", False)),
                                     str(body.get("evidence", "")),
                                     actor=str(body.get("actor", "user")))
    except Exception as exc:
        raise _err(exc)


# ---------------- reminders ----------------

@router.post("/reminders", status_code=201)
def create_reminder(body: dict):
    try:
        return t.create_reminder(str(body.get("task_id", "")),
                                 int(body.get("fire_at") or 0))
    except Exception as exc:
        raise _err(exc)


@router.get("/reminders")
def list_reminders(task_id: str = "", status: str = Query(default="")):
    return {"reminders": t.list_reminders(task_id, status)}


@router.post("/reminders/{rid}/snooze")
def snooze(rid: str, body: dict):
    try:
        return t.snooze_reminder(rid, int(body.get("until") or 0))
    except Exception as exc:
        raise _err(exc)


@router.post("/reminders/{rid}/dismiss")
def dismiss_reminder(rid: str):
    try:
        return t.dismiss_reminder(rid)
    except Exception as exc:
        raise _err(exc)


@router.post("/reminders/evaluate")
def evaluate(body: dict | None = None):
    threshold = float((body or {}).get("threshold", 2.0))
    return {"notified": t.evaluate_reminders(threshold=threshold)}


# ---------------- notifications ----------------

@router.get("/notifications")
def list_ntf(status: str = ""):
    return {"notifications": t.list_notifications(status)}


@router.get("/notifications/unread-count")
def unread():
    return {"unread": t.unread_count()}


@router.post("/notifications/{nid}/read")
def mark_read(nid: str):
    try:
        return t.mark_notification(nid, "read")
    except Exception as exc:
        raise _err(exc)


@router.post("/notifications/{nid}/dismiss")
def dismiss_ntf(nid: str):
    try:
        return t.mark_notification(nid, "dismissed")
    except Exception as exc:
        raise _err(exc)
