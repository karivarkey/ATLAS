import json
import sqlite3
import sys

c = sqlite3.connect("data/atlas.db")
eid = sys.argv[1] if len(sys.argv) > 1 else None
if not eid:
    row = c.execute("SELECT id FROM executions ORDER BY updated_at DESC LIMIT 1").fetchone()
    eid = row[0]
print("execution:", eid)
for (seq, typ, payload) in c.execute(
    "SELECT seq, type, payload_json FROM events WHERE execution_id=? ORDER BY seq", (eid,)
):
    p = json.loads(payload)
    if typ == "text_delta":
        print(f"{seq} TEXT: {p.get('delta','')[:150]!r}")
    elif typ in ("tool_started", "tool_completed", "tool_failed"):
        print(f"{seq} {typ} {p.get('name')}: {str(p.get('summary') or p.get('error') or p.get('output',''))[:220]!r}")
    elif typ == "question_asked":
        print(f"{seq} QUESTION: {p.get('question','')[:400]!r}")
    elif typ == "status_changed":
        print(f"{seq} STATUS: {p.get('from')} -> {p.get('to')}")
    else:
        print(f"{seq} {typ}")
