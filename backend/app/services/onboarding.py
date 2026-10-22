"""First-run onboarding: an LLM-led baby-steps interview that produces a
real USER.md profile. Completion is signaled by a fenced
```onboarding_complete {...}``` block in an assistant message, detected in
_finalize_message. Normal chats never emit that fence, so they're unaffected.
"""
from __future__ import annotations

import json
import logging
import re

from .. import db

log = logging.getLogger("atlas.onboarding")

FENCE_RE = re.compile(r"```onboarding_complete\s*(\{.*?\})\s*```", re.DOTALL)

ONBOARD_PROMPT = """You are Atlas's friendly setup guide. A new user just opened Atlas for the
first time. Your job: learn what you need to help them well, one small step at a time.

Rules:
- Be warm, brief, and concrete. This is a chat, not a form.
- Ask AT MOST two short questions per turn. React to each answer before moving on.
- Cover, in order: (1) name + what to call them, (2) what they build/work on,
  (3) languages/tools/stack, (4) current projects, (5) how they like answers
  (short vs detailed), (6) anything to avoid. Skip what they already told you.
- Do NOT use any tools. Just talk.
- When you have enough (name + work + stack + at least one preference), write a
  warm closing line, then on its own lines emit EXACTLY:
  ```onboarding_complete
  {"name": "...", "role": "...", "projects": [...], "stack": [...],
   "preferences": {"answer_style": "...", "language": "en"},
   "constraints": [...], "notes": "..."}
  ```
  Keep values short strings. Omit nothing with nulls — use "" or [].
- Never emit the fence until you are done asking questions.
"""


def is_onboarded() -> bool:
    row = db.query_one("SELECT value_json FROM settings WHERE key='onboarded'")
    if not row:
        return False
    try:
        return bool(json.loads(row["value_json"]))
    except Exception:
        return False


def set_onboarded(done: bool = True, how: str = "wizard") -> None:
    db.execute("INSERT INTO settings (key, value_json) VALUES ('onboarded', ?)"
               " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
               (json.dumps(done),))
    db.audit("onboarding.done" if done else "onboarding.reopened", {"how": how})


def parse_completion(text: str) -> dict | None:
    m = FENCE_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def apply_profile(profile: dict) -> int:
    """Write the initial USER.md from the interview. Returns version."""
    from . import memory_files as mf

    name = str(profile.get("name", "")).strip()
    lines = ["# USER.md — working context", "",
             "Created by the first-run onboarding interview. Edit freely in Settings.",
             ""]
    if name:
        lines += [f"## Identity", f"- Name: {name}",
                  f"- Role: {profile.get('role', '')}".rstrip(), ""]
    if profile.get("projects"):
        lines += ["## Projects"] + [f"- {p}" for p in profile["projects"][:10] if str(p).strip()] + [""]
    if profile.get("stack"):
        lines += ["## Stack"] + [f"- {s}" for s in profile["stack"][:15] if str(s).strip()] + [""]
    prefs = profile.get("preferences") or {}
    if isinstance(prefs, dict) and prefs:
        lines += ["## Preferences"] + [f"- {k}: {v}" for k, v in list(prefs.items())[:10]] + [""]
    if profile.get("constraints"):
        lines += ["## Constraints"] + [f"- {c}" for c in profile["constraints"][:10] if str(c).strip()] + [""]
    if profile.get("notes"):
        lines += ["## Notes", str(profile["notes"])[:1000], ""]
    lines += ["## Environment", "- Windows 11, local-first setup. Shell: cmd.exe via the agent runtime.", ""]
    content = "\n".join(lines).strip() + "\n"
    version = mf.write_file("USER.md", content, source="onboarding")
    from . import dream as _dream
    rid = db.new_id("mem")
    now = db.now_ms()
    db.execute(
        "INSERT INTO memory_records (id, scope_id, memory_type, subject, predicate, object_value,"
        " summary, confidence, status, valid_from, source_conversation_ids, source_message_ids,"
        " source_chunk_ids, supersedes_memory_id, created_at, updated_at)"
        " VALUES (?, 'user:default', 'preference', ?, 'onboarding_profile', ?, ?, 0.85,"
        " 'active', ?, '[]', '[]', '[]', '', ?, ?)",
        (rid, name or "user", json.dumps(profile)[:800],
         f"Onboarding profile for {name or 'user'}".strip(), now, now, now))
    _dream._hist(rid, "created", {"source": "onboarding"})
    db.audit("onboarding.profile", {"record": rid, "version": version})
    return version


def maybe_finish_from_text(text: str) -> dict | None:
    """Called on every finalized assistant message. Returns version info or None."""
    profile = parse_completion(text or "")
    if not profile:
        return None
    try:
        version = apply_profile(profile)
    except Exception as exc:
        log.warning("onboarding apply failed: %s", exc)
        return None
    set_onboarded(True, how="wizard")
    return {"user_version": version, "name": profile.get("name", "")}
