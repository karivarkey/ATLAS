"""Re-run the user's exact failing case on the fixed routing:
model=opencode-go/muse-spark-1.3-contributor, prompt asks for system stats
with tool calling. PASS = completed + a shell_exec tool result present.
"""
import json
import time
import urllib.request

B = "http://127.0.0.1:8765"
MODEL = "opencode-go/muse-spark-1.3-contributor"


def req(method, path, body=None):
    r = urllib.request.Request(
        B + path,
        data=json.dumps(body or {}).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


c = req("POST", "/api/v1/conversations", {"title": "muse-spark retry"})
cid = c["id"]
sent = req("POST", f"/api/v1/conversations/{cid}/messages",
           {"content": "You have tool calling. Tell me some system stats: "
                       "run a shell command to show OS, CPU count and free memory, "
                       "then summarize the output briefly.",
            "model": MODEL})
eid = sent["execution_id"]
print("execution:", eid)

deadline = time.time() + 600
status = ""
while time.time() < deadline:
    time.sleep(5)
    snap = req("GET", f"/api/v1/executions/{eid}")
    status = snap["status"]
    print(f"status={status} step={snap['current_step']}")
    if status in ("completed", "failed", "cancelled", "timed_out", "interrupted"):
        if snap.get("error"):
            print("error:", snap["error"][:500])
        break

evs = req("GET", f"/api/v1/executions/{eid}/events")["events"]
shell_ok = [e for e in evs if e["type"] == "tool_completed"
            and e["payload"].get("name") == "shell_exec"]
for e in shell_ok[:3]:
    print(">", str(e["payload"].get("output", ""))[:250].replace("\n", " | "))
print("E2E RESULT:", "PASS" if (status == "completed" and shell_ok) else
      f"CHECK (status={status}, shell_ok={bool(shell_ok)})")
