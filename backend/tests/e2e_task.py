"""Real task E2E with the server default model.

Task: write hello.py containing exactly print("HELLO-ATLAS") on one line,
then run `python hello.py` and report the output. PASS requires exit=0
with HELLO-ATLAS in a shell tool result.
"""
import json
import time
import urllib.request

B = "http://127.0.0.1:8765"


def _req_noerr(method, path, body=None):
    r = urllib.request.Request(
        B + path,
        data=json.dumps(body or {}).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


# Always follow the server default (no hardcoded model).
MODEL = _req_noerr("GET", "/api/v1/config")["default_model"]
print("using model:", MODEL)


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
        print("HTTP", e.code, e.read().decode()[:300])
        raise


c = req("POST", "/api/v1/conversations", {"title": "e2e-task"})
cid = c["id"]
sent = req("POST", f"/api/v1/conversations/{cid}/messages",
           {"content": 'Use the fs_write tool to create file hello.py with exactly this one line:\n'
                       'print("HELLO-ATLAS")\n'
                       'Then use shell_exec to run: python hello.py\n'
                       'Report the exact command output. Do not ask questions; just do it.',
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
        break
    if status == "waiting_input":
        req("POST", f"/api/v1/executions/{eid}/answer",
            {"text": "Proceed with your best judgment; do not ask further questions."})
        print("answered question, continuing")

evs = req("GET", f"/api/v1/executions/{eid}/events")["events"]
shell_ok = [e for e in evs
            if e["type"] == "tool_completed" and e["payload"].get("name") == "shell_exec"
            and "HELLO-ATLAS" in str(e["payload"].get("output", ""))]
print("shell outputs with marker:", len(shell_ok))
for e in shell_ok:
    print(">", str(e["payload"]["output"])[:200].replace("\n", " | "))
print("E2E RESULT:", "PASS" if (status == "completed" and shell_ok) else
      f"CHECK (status={status}, shell_ok={bool(shell_ok)})")
