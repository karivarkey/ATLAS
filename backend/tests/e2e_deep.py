"""Live deep check: stale-write conflict, AI task execute+verify, reminders."""
import json
import time
import urllib.request

B = "http://127.0.0.1:8765"


def req(method, path, body=None):
    r = urllib.request.Request(
        B + path,
        data=json.dumps(body or {}).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        return json.loads(urllib.request.urlopen(r, timeout=120).read())
    except urllib.error.HTTPError as e:
        return {"__http": e.code, "body": e.read().decode()[:400]}


print("== stale write guard ==")
n = req("POST", "/api/v1/notes", {"path": "Stale.md", "content": "# S\n\nv1\n"})
v1, h1 = n["version"], n["content_hash"]
print("v1:", v1, h1[:8])
# external edit bumps version
req("PUT", "/api/v1/notes?path=Stale.md",
    {"content": "# S\n\nv2 external\n", "expected_version": v1, "expected_hash": h1})
n2 = req("GET", "/api/v1/notes?path=Stale.md")
print("now v%s" % n2["version"])
stale = req("PUT", "/api/v1/notes?path=Stale.md",
            {"content": "stale overwrite", "expected_version": v1, "expected_hash": h1})
print("stale write ->", stale.get("__http"), str(stale.get("body"))[:160])

print("== AI task execute + verify ==")
t = req("POST", "/api/v1/tasks", {"title": "Write vault greeting note",
                                  "description": "Create file Greet.md containing exactly: hello vault",
                                  "project": "Atlas"})
ex = req("POST", f"/api/v1/tasks/{t['id']}/execute", {})
print("exec:", ex["execution_id"], "conv:", ex["conversation_id"])
deadline = time.time() + 420
status = ""
while time.time() < deadline:
    time.sleep(5)
    s = req("GET", f"/api/v1/executions/{ex['execution_id']}")
    status = s["status"]
    if status in ("completed", "failed", "cancelled", "timed_out"):
        break
print("attempt status:", status)
ver = req("POST", f"/api/v1/tasks/{t['id']}/verify",
          {"execution_id": ex["execution_id"], "criteria_met": True,
           "evidence": "hello vault file created, verified via vault read"})
print("verified:", ver.get("verified"), "| task:", ver.get("task", {}).get("status"))
note = req("GET", "/api/v1/notes?path=Greet.md")
print("Greet.md:", str(note.get("content", ""))[:60].replace("\n", " | "))

print("== overdue reminder ==")
past = 1000 * (int(time.time() * 1000) - 86400000)
t2 = req("POST", "/api/v1/tasks", {"title": "Overdue live check",
                                  "priority": "high", "due_at": past})
print("created overdue:", t2["id"])
time.sleep(75)  # maintenance loop runs every 60s
un = req("GET", "/api/v1/notifications/unread-count")
print("unread:", un)
ns = req("GET", "/api/v1/notifications?status=unread")
for x in ns.get("notifications", [])[:5]:
    print("-", x.get("kind"), "|", x.get("title", "")[:60])
print("LIVE DEEP CHECK DONE")
