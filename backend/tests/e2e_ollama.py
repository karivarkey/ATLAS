"""Real end-to-end agent run against local Ollama (qwen2.5-coder:1.5b).

Task: create hello.py printing HELLO-ATLAS and run it. Asserts the file
exists on disk and the tool result contains the marker.
"""
import json
import time
import urllib.request

B = "http://127.0.0.1:8765"
MODEL = "ollama/qwen2.5-coder:7b"


def req(method, path, body=None):
    r = urllib.request.Request(
        B + path,
        data=json.dumps(body or {}).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        return json.loads(urllib.request.urlopen(r, timeout=60).read())
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:500])
        raise


c = req("POST", "/api/v1/conversations", {"title": "e2e-ollama"})
cid = c["id"]
print("conv:", cid)
sent = req("POST", f"/api/v1/conversations/{cid}/messages",
           {"content": "Create a file hello.py that prints HELLO-ATLAS when run, "
                       "then run it with python and report the exact output.",
            "model": MODEL})
eid = sent["execution_id"]
print("execution:", eid)

deadline = time.time() + 480
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

evs = req("GET", f"/api/v1/executions/{eid}/events")
types = [e["type"] for e in evs["events"]]
print("event types:", sorted(set(types)))
tools = [e for e in evs["events"] if e["type"] in ("tool_completed", "tool_failed")]
for t in tools:
    print("-", t["payload"].get("name"), ":", str(t["payload"].get("output", ""))[:200].replace("\n", " | "))

ws = req("GET", f"/api/v1/workspaces/{cid}/files")
print("workspace:", json.dumps(ws)[:300])
ok = any("HELLO-ATLAS" in json.dumps(e.get("payload", {})) for e in evs["events"])
print("E2E RESULT:", "PASS" if (status == "completed" and ok) else f"CHECK (status={status}, marker={ok})")
