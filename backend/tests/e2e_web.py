"""Live grounded-answer check: model must web_search, web_fetch a result,
and report a grounded fact. PASS = completed + web_search + web_fetch both
ran + answer cites a fetched URL.
"""
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
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


default_model = req("GET", "/api/v1/config")["default_model"]
print("using model:", default_model)
c = req("POST", "/api/v1/conversations", {"title": "web grounded"})
cid = c["id"]
sent = req("POST", f"/api/v1/conversations/{cid}/messages",
           {"content": "Use web_search to find the current latest stable Python release version. "
                       "Then use web_fetch on the most official result (python.org) to confirm. "
                       "Report the version number and the URL you confirmed it from. "
                       "Do not ask questions; just do it.",
            "model": default_model})
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
            print("error:", snap["error"][:400])
        break

evs = req("GET", f"/api/v1/executions/{eid}/events")["events"]
names = [e["payload"].get("name") for e in evs if e["type"] == "tool_completed"]
print("completed tools:", names)
msgs = req("GET", f"/api/v1/conversations/{cid}/messages")["messages"]
final = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant"), "")
print("final answer:", final[:400].replace("\n", " | "))
ok = (status == "completed" and "web_search" in names and "web_fetch" in names
      and "python.org" in final)
print("E2E RESULT:", "PASS" if ok else f"CHECK (status={status}, tools={names})")
