"""Structured tasks: CRUD, status machine, note links, recurrence, reminders.

- Statuses: inbox → planned → in_progress ⇄ (waiting_input | waiting_approval)
  → completed → archived, plus cancelled → archived. Terminal states can only
  be left via reopen (→ planned).
- Occurrence generation is idempotent: (series_id, occurrence_key) is unique,
  so restarts and double-runs never duplicate.
- Reminders score importance × deadline proximity and are gated by
  snooze / dismiss / quiet hours, with dedupe so there is no infinite nagging
  (at most one unread reminder notification per task per day).
- AI execution goes through services.executions.submit for real; a task is
  only marked completed via verify_task_attempt with explicit criteria —
  never on model text alone.
"""
from __future__ import annotations

import datetime as _dt
import json

from .. import config, db

STATUSES = ("inbox", "planned", "in_progress", "waiting_input", "waiting_approval",
            "completed", "cancelled", "archived")
PRIORITIES = ("critical", "high", "normal", "low")
TERMINAL = ("completed", "cancelled", "archived")

TRANSITIONS: dict[str, set[str]] = {
    "inbox": {"planned", "in_progress", "cancelled"},
    "planned": {"in_progress", "cancelled", "inbox"},
    "in_progress": {"waiting_input", "waiting_approval", "completed", "cancelled", "planned"},
    "waiting_input": {"in_progress", "completed", "cancelled"},
    "waiting_approval": {"in_progress", "completed", "cancelled"},
    "completed": {"archived"},
    "cancelled": {"archived"},
    "archived": {"planned"},  # reopen
}

PRIORITY_W = {"critical": 4.0, "high": 3.0, "normal": 2.0, "low": 1.0}

QUIET_START = 22  # 22:00 local
QUIET_END = 7     # …to 07:00 local


class TaskError(ValueError):
    pass


# ---------------- CRUD ----------------

def _row(task_id: str):
    return db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))


def _serialize(r) -> dict:
    d = dict(r)
    try:
        d["eligibility"] = json.loads(d.get("eligibility") or "{}")
    except Exception:
        d["eligibility"] = {}
    d["approval_required"] = bool(d.get("approval_required"))
    d["notes"] = [dict(n) for n in db.query(
        "SELECT n.id AS note_id, n.path, n.title FROM task_notes tn"
        " JOIN notes n ON n.id = tn.note_id WHERE tn.task_id = ?", (d["id"],))]
    return d


def _history(task_id: str, action: str, detail: dict | None = None, actor: str = "user") -> None:
    db.execute("INSERT INTO task_history (task_id, action, detail_json, actor, created_at)"
               " VALUES (?, ?, ?, ?, ?)",
               (task_id, action, json.dumps(detail or {}), actor, db.now_ms()))


def create_task(title: str, description: str = "", status: str = "inbox",
                priority: str = "normal", due_at: int = 0, due_tz: str = "",
                project: str = "", source_conv: str = "", source_msg: str = "",
                recurrence: str = "", recur_end: int = 0, recur_max: int = 0,
                approval_required: bool = False, eligibility: dict | None = None,
                actor: str = "user") -> dict:
    if not (title or "").strip():
        raise TaskError("title is required")
    if status not in STATUSES:
        raise TaskError(f"unknown status: {status}")
    if priority not in PRIORITIES:
        raise TaskError(f"unknown priority: {priority}")
    if recurrence:
        _validate_recurrence(recurrence)
    tid = db.new_id("task")
    now = db.now_ms()
    db.execute(
        "INSERT INTO tasks (id, title, description, status, priority, due_at, due_tz, project,"
        " source_conv, source_msg, recurrence, recur_end, recur_max, approval_required,"
        " eligibility, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, title.strip()[:300], description or "", status, priority, int(due_at or 0),
         due_tz[:60], (project or "")[:120], source_conv, source_msg, recurrence,
         int(recur_end or 0), int(recur_max or 0), 1 if approval_required else 0,
         json.dumps(eligibility or {}), now, now))
    _history(tid, "created", {"status": status, "priority": priority}, actor)
    db.audit("task.create", {"id": tid, "title": title[:100]}, actor=actor)
    return _serialize(_row(tid))


def get_task(task_id: str) -> dict:
    r = _row(task_id)
    if not r:
        raise KeyError(f"task not found: {task_id}")
    return _serialize(r)


def list_tasks(status: str = "", project: str = "", priority: str = "",
               bucket: str = "", limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if status:
        if status not in STATUSES:
            raise TaskError(f"unknown status: {status}")
        sql += " AND status = ?"
        params.append(status)
    if project:
        sql += " AND project = ?"
        params.append(project)
    if priority:
        sql += " AND priority = ?"
        params.append(priority)
    sql += " ORDER BY due_at ASC, created_at DESC LIMIT ?"
    params.append(min(limit, 500))
    rows = [_serialize(r) for r in db.query(sql, tuple(params))]
    if bucket:
        rows = [t for t in rows if _bucket(t) == bucket]
    return rows


def _bucket(t: dict) -> str:
    """List filter bucket: today | overdue | waiting | inbox | project:<name>."""
    now = db.now_ms()
    day_start = now - (now % 86400000)
    st = t["status"]
    if st in ("waiting_input", "waiting_approval"):
        return "waiting"
    if st == "inbox":
        return "inbox"
    due = int(t.get("due_at") or 0)
    if due and due < now and st not in TERMINAL:
        return "overdue"
    if due and due < day_start + 86400000 and st not in TERMINAL:
        return "today"
    return "all"


def update_task(task_id: str, actor: str = "user", **fields) -> dict:
    r = _row(task_id)
    if not r:
        raise KeyError(f"task not found: {task_id}")
    if "status" in fields and fields["status"] != r["status"]:
        raise TaskError("use transition_task (POST /tasks/:id/transition) to change status")
    allowed = {"title", "description", "priority", "due_at", "due_tz", "project",
               "recurrence", "recur_end", "recur_max", "approval_required", "eligibility",
               "source_conv", "source_msg"}
    updates = {}
    for k, v in fields.items():
        if k not in allowed:
            raise TaskError(f"unknown field: {k}")
        updates[k] = v
    if "priority" in updates and updates["priority"] not in PRIORITIES:
        raise TaskError(f"unknown priority: {updates['priority']}")
    if "recurrence" in updates and updates["recurrence"]:
        _validate_recurrence(updates["recurrence"])
    if not updates:
        return _serialize(r)
    if "eligibility" in updates and isinstance(updates["eligibility"], dict):
        updates["eligibility"] = json.dumps(updates["eligibility"])
    if "approval_required" in updates:
        updates["approval_required"] = 1 if updates["approval_required"] else 0
    if "title" in updates:
        updates["title"] = (updates["title"] or "").strip()[:300]
        if not updates["title"]:
            raise TaskError("title is required")
    sets = ", ".join(f"{k} = ?" for k in updates)
    db.execute(f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?",
               (*updates.values(), db.now_ms(), task_id))
    _history(task_id, "updated", {k: str(v)[:200] for k, v in updates.items()}, actor)
    return _serialize(_row(task_id))


def transition_task(task_id: str, to: str, actor: str = "user", note: str = "") -> dict:
    r = _row(task_id)
    if not r:
        raise KeyError(f"task not found: {task_id}")
    if to not in STATUSES:
        raise TaskError(f"unknown status: {to}")
    frm = r["status"]
    if to != frm and to not in TRANSITIONS.get(frm, set()):
        raise TaskError(f"illegal transition {frm} → {to}")
    db.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
               (to, db.now_ms(), task_id))
    _history(task_id, "transition", {"from": frm, "to": to, "note": note[:500]}, actor)
    db.audit("task.transition", {"id": task_id, "from": frm, "to": to}, actor=actor)
    return _serialize(_row(task_id))


def delete_task(task_id: str, actor: str = "user") -> dict:
    if not _row(task_id):
        raise KeyError(f"task not found: {task_id}")
    db.execute("DELETE FROM task_notes WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM reminders WHERE task_id = ?", (task_id,))
    db.execute("UPDATE notifications SET status='dismissed' WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    _history(task_id, "deleted", {}, actor)
    db.audit("task.delete", {"id": task_id}, actor=actor)
    return {"ok": True, "id": task_id}


def task_history(task_id: str) -> list[dict]:
    if not _row(task_id):
        raise KeyError(f"task not found: {task_id}")
    return [dict(r) for r in db.query(
        "SELECT * FROM task_history WHERE task_id = ? ORDER BY created_at ASC", (task_id,))]


# ---------------- note links (stable across renames: keyed by note id) ----------------

def link_note(task_id: str, note_path: str, actor: str = "user") -> dict:
    from . import vault as _vault
    if not _row(task_id):
        raise KeyError(f"task not found: {task_id}")
    row = _vault.note_by_path(note_path)
    if not row:
        raise KeyError(f"note not found: {note_path}")
    db.execute("INSERT OR IGNORE INTO task_notes (task_id, note_id, created_at)"
               " VALUES (?, ?, ?)", (task_id, row["id"], db.now_ms()))
    _history(task_id, "note_linked", {"note_id": row["id"], "path": row["path"]}, actor)
    return _serialize(_row(task_id))


def unlink_note(task_id: str, note_id: str, actor: str = "user") -> dict:
    if not _row(task_id):
        raise KeyError(f"task not found: {task_id}")
    db.execute("DELETE FROM task_notes WHERE task_id = ? AND note_id = ?",
               (task_id, note_id))
    _history(task_id, "note_unlinked", {"note_id": note_id}, actor)
    return _serialize(_row(task_id))


# ---------------- recurrence (RRULE-lite) ----------------

def _validate_recurrence(rec: str) -> None:
    if rec in ("daily", "weekly", "monthly", "weekdays"):
        return
    if rec.startswith("interval:"):
        try:
            n = int(rec.split(":", 1)[1])
        except ValueError:
            raise TaskError(f"bad recurrence (want interval:N days): {rec}")
        if n < 1 or n > 365:
            raise TaskError(f"bad recurrence interval: {rec}")
        return
    raise TaskError(f"unknown recurrence: {rec} "
                    "(daily|weekly|monthly|weekdays|interval:N)")


def _matches_rule(day: _dt.date, rec: str, anchor: _dt.date) -> bool:
    if rec == "daily":
        return True
    if rec == "weekdays":
        return day.weekday() < 5
    if rec == "weekly":
        return day.weekday() == anchor.weekday()
    if rec == "monthly":
        return day.day == min(anchor.day, _month_len(day.year, day.month))
    if rec.startswith("interval:"):
        n = int(rec.split(":", 1)[1])
        return (day - anchor).days % n == 0
    return False


def _month_len(year: int, month: int) -> int:
    if month == 12:
        nxt = _dt.date(year + 1, 1, 1)
    else:
        nxt = _dt.date(year, month + 1, 1)
    return (nxt - _dt.date(year, month, 1)).days


def occurrence_dates(recurrence: str, anchor_ms: int, start_ms: int,
                     count: int = 30) -> list[int]:
    """Pure date math: occurrence midnights (UTC ms) strictly after start_ms."""
    _validate_recurrence(recurrence)
    anchor = _dt.datetime.fromtimestamp((anchor_ms or start_ms) / 1000,
                                        tz=_dt.timezone.utc).date()
    out: list[int] = []
    day = _dt.datetime.fromtimestamp(start_ms / 1000, tz=_dt.timezone.utc).date()
    guard = 0
    while len(out) < count and guard < 1500:
        guard += 1
        day += _dt.timedelta(days=1)
        if _matches_rule(day, recurrence, anchor):
            ts = int(_dt.datetime(day.year, day.month, day.day,
                                  tzinfo=_dt.timezone.utc).timestamp() * 1000)
            out.append(ts)
    return out


def generate_occurrences(task_id: str, horizon_days: int = 30) -> dict:
    """Idempotent: INSERT OR IGNORE on (series_id, occurrence_key)."""
    r = _row(task_id)
    if not r:
        raise KeyError(f"task not found: {task_id}")
    if not r["recurrence"]:
        raise TaskError("task has no recurrence")
    now = db.now_ms()
    anchor = int(r["due_at"] or now)
    start_day = now - (now % 86400000)
    due_dates = occurrence_dates(r["recurrence"], anchor, start_day,
                                 count=max(horizon_days, 1))
    existing = db.query_one("SELECT COUNT(*) AS n FROM task_occurrences WHERE series_id = ?",
                            (task_id,))
    made = 0
    for ts in due_dates:
        if int(r["recur_end"] or 0) and ts > int(r["recur_end"]):
            continue
        if int(r["recur_max"] or 0) and int(existing["n"]) + made >= int(r["recur_max"]):
            break
        key = _dt.datetime.fromtimestamp(ts / 1000, tz=_dt.timezone.utc).strftime("%Y-%m-%d")
        cur = db.execute(
            "INSERT OR IGNORE INTO task_occurrences (id, series_id, occurrence_key, due_at,"
            " status, created_at) VALUES (?, ?, ?, ?, 'scheduled', ?)",
            (db.new_id("occ"), task_id, key, ts, now))
        made += cur.rowcount
    _history(task_id, "occurrences_generated", {"made": made, "horizon_days": horizon_days})
    return {"task_id": task_id, "new": made,
            "total": int(existing["n"]) + made}


def list_occurrences(task_id: str) -> list[dict]:
    if not _row(task_id):
        raise KeyError(f"task not found: {task_id}")
    return [dict(r) for r in db.query(
        "SELECT * FROM task_occurrences WHERE series_id = ? ORDER BY due_at ASC", (task_id,))]


# ---------------- reminders ----------------

def in_quiet_hours(now_ms: int, start: int = QUIET_START, end: int = QUIET_END) -> bool:
    hour = _dt.datetime.fromtimestamp(now_ms / 1000).hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def reminder_score(task: dict, now_ms: int | None = None) -> float:
    """importance × deadline proximity; 0 when snoozed/dismissed/quiet/off-terminal."""
    now = now_ms or db.now_ms()
    if task.get("status") in TERMINAL:
        return 0.0
    if in_quiet_hours(now):
        return 0.0
    rems = db.query("SELECT * FROM reminders WHERE task_id = ? ORDER BY created_at DESC LIMIT 5",
                    (task["id"],))
    for m in rems:
        if m["status"] == "dismissed":
            return 0.0
        if m["status"] == "snoozed" and int(m["snoozed_until"] or 0) > now:
            return 0.0
    importance = PRIORITY_W.get(task.get("priority", "normal"), 2.0)
    due = int(task.get("due_at") or 0)
    if not due:
        proximity = 0.25
    else:
        hours_left = (due - now) / 3600000.0
        if hours_left < 0:
            proximity = 3.0 + min(2.0, abs(hours_left) / 48.0)  # overdue escalates, capped
        elif hours_left < 2:
            proximity = 2.5
        elif hours_left < 24:
            proximity = 1.5
        elif hours_left < 72:
            proximity = 1.0
        else:
            proximity = 0.5
    return round(importance * proximity, 3)


def create_reminder(task_id: str, fire_at: int, actor: str = "user") -> dict:
    if not _row(task_id):
        raise KeyError(f"task not found: {task_id}")
    rid = db.new_id("rem")
    now = db.now_ms()
    db.execute("INSERT INTO reminders (id, task_id, fire_at, status, snoozed_until,"
               " created_at, updated_at) VALUES (?, ?, ?, 'pending', 0, ?, ?)",
               (rid, task_id, int(fire_at), now, now))
    _history(task_id, "reminder_created", {"fire_at": fire_at}, actor)
    return dict(db.query_one("SELECT * FROM reminders WHERE id = ?", (rid,)))


def list_reminders(task_id: str = "", status: str = "") -> list[dict]:
    sql = "SELECT * FROM reminders WHERE 1=1"
    params: list = []
    if task_id:
        sql += " AND task_id = ?"
        params.append(task_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY fire_at ASC"
    return [dict(r) for r in db.query(sql, tuple(params))]


def snooze_reminder(reminder_id: str, until_ms: int, actor: str = "user") -> dict:
    m = db.query_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
    if not m:
        raise KeyError("reminder not found")
    db.execute("UPDATE reminders SET status='snoozed', snoozed_until=?, updated_at=? WHERE id=?",
               (int(until_ms), db.now_ms(), reminder_id))
    _history(m["task_id"], "reminder_snoozed", {"until": until_ms}, actor)
    return dict(db.query_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,)))


def dismiss_reminder(reminder_id: str, actor: str = "user") -> dict:
    m = db.query_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
    if not m:
        raise KeyError("reminder not found")
    db.execute("UPDATE reminders SET status='dismissed', updated_at=? WHERE id=?",
               (db.now_ms(), reminder_id))
    _history(m["task_id"], "reminder_dismissed", {}, actor)
    return dict(db.query_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,)))


def evaluate_reminders(now_ms: int | None = None, threshold: float = 2.0) -> list[dict]:
    """Create deduped notifications for tasks that merit a nudge.

    Anti-nag: at most one unread reminder notification per task, and one
    dedupe key per task per day. Snoozed/dismissed/quiet-hour tasks score 0.
    """
    now = now_ms or db.now_ms()
    day = _dt.datetime.fromtimestamp(now / 1000, tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    created = []
    for r in db.query("SELECT * FROM tasks WHERE status NOT IN ('completed','cancelled','archived')"):
        t = _serialize(r)
        score = reminder_score(t, now)
        if score < threshold:
            continue
        unread = db.query_one("SELECT id FROM notifications WHERE task_id = ? AND kind='reminder'"
                              " AND status='unread' LIMIT 1", (t["id"],))
        if unread:
            continue  # already nagging — don't pile on
        n = notify("reminder", f"Reminder: {t['title']}",
                   _reminder_body(t, score), task_id=t["id"],
                   dedupe_key=f"remind:{t['id']}:{day}")
        if n.get("created"):
            created.append({**n["notification"], "score": score})
    return created


def _reminder_body(t: dict, score: float) -> str:
    due = int(t.get("due_at") or 0)
    if due:
        dt = _dt.datetime.fromtimestamp(due / 1000).strftime("%Y-%m-%d %H:%M")
        return f"Due {dt} · priority {t['priority']} · score {score}"
    return f"Priority {t['priority']} · score {score}"


# ---------------- notifications ----------------

def notify(kind: str, title: str, body: str = "", task_id: str = "",
           note_id: str = "", dedupe_key: str = "") -> dict:
    """Deduped notification. With a dedupe_key this is INSERT OR IGNORE —
    repeat callers never create a second row."""
    if dedupe_key:
        cur = db.execute(
            "INSERT OR IGNORE INTO notifications (id, kind, title, body, task_id, note_id,"
            " dedupe_key, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'unread', ?)",
            (db.new_id("ntf"), kind, title[:300], (body or "")[:2000],
             task_id, note_id, dedupe_key, db.now_ms()))
        if cur.rowcount == 0:
            existing = db.query_one("SELECT * FROM notifications WHERE dedupe_key = ?",
                                    (dedupe_key,))
            return {"notification": dict(existing), "created": False}
        row = db.query_one("SELECT * FROM notifications WHERE dedupe_key = ?", (dedupe_key,))
        return {"notification": dict(row), "created": True}
    nid = db.new_id("ntf")
    db.execute("INSERT INTO notifications (id, kind, title, body, task_id, note_id,"
               " dedupe_key, status, created_at) VALUES (?, ?, ?, ?, ?, ?, NULL, 'unread', ?)",
               (nid, kind, title[:300], (body or "")[:2000], task_id, note_id, db.now_ms()))
    return {"notification": dict(db.query_one("SELECT * FROM notifications WHERE id = ?",
                                              (nid,))), "created": True}


def list_notifications(status: str = "", limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM notifications WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(min(limit, 300))
    return [dict(r) for r in db.query(sql, tuple(params))]


def unread_count() -> int:
    r = db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE status = 'unread'")
    return int(r["n"]) if r else 0


def mark_notification(nid: str, status: str) -> dict:
    if status not in ("read", "dismissed", "unread"):
        raise TaskError(f"unknown notification status: {status}")
    if not db.query_one("SELECT id FROM notifications WHERE id = ?", (nid,)):
        raise KeyError("notification not found")
    db.execute("UPDATE notifications SET status = ? WHERE id = ?", (status, nid))
    return dict(db.query_one("SELECT * FROM notifications WHERE id = ?", (nid,)))


# ---------------- AI execution ----------------

async def execute_task(task_id: str, conversation_id: str = "", model: str = "",
                       actor: str = "user") -> dict:
    """Run a task with a REAL agent execution. Records an attempt; the task
    moves to in_progress. Completion happens only via verify_task_attempt."""
    from . import executions
    from . import noteagent as _na

    r = _row(task_id)
    if not r:
        raise KeyError(f"task not found: {task_id}")
    if r["status"] in ("completed", "archived"):
        raise TaskError(f"task is {r['status']}")
    t = _serialize(r)
    ctx = _na.build_task_context(task_id)
    notes_txt = "\n".join(
        f"--- note {n['path']} (v{n['version']}) ---\n{n['content'][:4000]}"
        for n in ctx.get("notes", [])) or "(no linked notes)"
    prompt = (f"[AI task {task_id}] {t['title']}\n\n{t['description'] or '(no description)'}\n\n"
              f"Linked notes (current versions):\n{notes_txt}\n\n"
              f"Available vault tools: note_read(path), note_write(path, content,"
              f" expected_version, expected_hash) — ALWAYS re-read a note right before writing it"
              f" and pass back its version+hash; task_get, task_update."
              f" File changes that belong in the knowledge base go through note_write"
              f" (vault), not the workspace filesystem.\n\n"
              "Completion criteria: the task description above must be visibly satisfied"
              " in the workspace or vault (files changed, tests run, output shown)."
              " Narrating intent without tool-verified changes does NOT count.")
    if not conversation_id:
        cid = db.new_id("conv")
        now = db.now_ms()
        db.execute("INSERT INTO conversations (id, title, model, workspace_id, created_at, updated_at)"
                   " VALUES (?, ?, ?, ?, ?, ?)",
                   (cid, f"Task: {t['title'][:60]}", model or config.DEFAULT_MODEL,
                    cid, now, now))
        conversation_id = cid
    res = await executions.submit(conversation_id, prompt, model or "")
    aid = db.new_id("att")
    db.execute("INSERT INTO task_attempts (id, task_id, execution_id, conversation_id,"
               " status, criteria_json, evidence, created_at, ended_at)"
               " VALUES (?, ?, ?, ?, 'running', '{}', '', ?, 0)",
               (aid, task_id, res["execution_id"], conversation_id, db.now_ms()))
    if r["status"] not in ("in_progress", "waiting_input", "waiting_approval"):
        db.execute("UPDATE tasks SET status='in_progress', updated_at=? WHERE id=?",
                   (db.now_ms(), task_id))
    _history(task_id, "ai_attempt", {"execution_id": res["execution_id"],
                                     "conversation_id": conversation_id}, actor)
    db.audit("task.execute", {"id": task_id, "execution": res["execution_id"]}, actor=actor)
    return {"task_id": task_id, "execution_id": res["execution_id"],
            "conversation_id": conversation_id, "attempt_id": aid}


def list_attempts(task_id: str) -> list[dict]:
    if not _row(task_id):
        raise KeyError(f"task not found: {task_id}")
    return [dict(r) for r in db.query(
        "SELECT * FROM task_attempts WHERE task_id = ? ORDER BY created_at ASC", (task_id,))]


def verify_task_attempt(task_id: str, execution_id: str, criteria_met: bool,
                        evidence: str = "", actor: str = "user") -> dict:
    """Mark an attempt verified. The task completes ONLY when the execution
    reached terminal `completed` AND explicit criteria are met with evidence.
    Model text alone never completes a task."""
    r = _row(task_id)
    if not r:
        raise KeyError(f"task not found: {task_id}")
    att = db.query_one("SELECT * FROM task_attempts WHERE task_id = ? AND execution_id = ?",
                       (task_id, execution_id))
    if not att:
        raise KeyError("attempt not found for this task/execution")
    from . import executions as _exec
    snap = _exec.get_snapshot(execution_id)
    exec_status = snap["status"] if snap else "unknown"
    ok = bool(criteria_met) and exec_status == "completed" and bool((evidence or "").strip())
    new_status = "verified" if ok else "unverified"
    db.execute("UPDATE task_attempts SET status=?, criteria_json=?, evidence=?, ended_at=?"
               " WHERE id=?",
               (new_status, json.dumps({"criteria_met": bool(criteria_met)}),
                (evidence or "")[:2000], db.now_ms(), att["id"]))
    if ok:
        db.execute("UPDATE tasks SET status='completed', updated_at=? WHERE id=?",
                   (db.now_ms(), task_id))
        _history(task_id, "ai_verified", {"execution_id": execution_id,
                                          "evidence": (evidence or "")[:500]}, actor)
        notify("task_completed", f"Task completed: {r['title']}",
               (evidence or "")[:500], task_id=task_id,
               dedupe_key=f"taskdone:{task_id}:{execution_id}")
    else:
        reason = ("execution status is "
                  f"'{exec_status}' (need 'completed')") if exec_status != "completed" else (
                      "criteria not met or evidence missing")
        _history(task_id, "ai_unverified",
                 {"execution_id": execution_id, "reason": reason}, actor)
    db.audit("task.verify", {"id": task_id, "execution": execution_id, "ok": ok},
             actor=actor)
    return {"task_id": task_id, "execution_id": execution_id,
            "execution_status": exec_status, "verified": ok,
            "task": _serialize(_row(task_id))}
