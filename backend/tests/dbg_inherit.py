import json
import sqlite3
import time
import urllib.request
from pathlib import Path

B = "http://127.0.0.1:8765"
DB = Path(__file__).resolve().parent.parent.parent / "data" / "atlas.db"


def req(method, path, body=None):
    r = urllib.request.Request(
        B + path,
        data=json.dumps(body or {}).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    return json.loads(urllib.request.urlopen(r, timeout=120).read())


def wait(eid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(4)
        s = req("GET", f"/api/v1/executions/{eid}")
        if s["status"] in ("completed", "failed", "cancelled", "timed_out", "interrupted"):
            return s
    return s


start = req("POST", "/api/v1/onboarding/start", {})
cid = start["conversation_id"]
wait(start["execution_id"])
r = req("POST", f"/api/v1/conversations/{cid}/messages",
        {"content": "dunno", "model": "opencode-go/muse-spark-1.3-contributor"})
s = wait(r["execution_id"])
c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row
row = c.execute("SELECT substr(system_prompt,1,40) AS sp, status FROM executions WHERE id = ?",
                (r["execution_id"],)).fetchone()
print("follow-up turn:", s["status"], "| system_prompt:", repr(row["sp"]))
m = c.execute("SELECT substr(content,1,300) AS t FROM messages WHERE conversation_id=?"
              " AND role='assistant' ORDER BY created_at DESC LIMIT 1", (cid,)).fetchone()
print("assistant said:", (m["t"] or "").replace("\n", " | "))
req("DELETE", f"/api/v1/conversations/{cid}")
print("cleaned up", cid)
