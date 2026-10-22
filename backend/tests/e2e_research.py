"""Live deep-research check: model must use web_research (or search+fetch),
then report with cited sources. PASS = completed + research tool ran +
final answer names 2+ source URLs.
"""
import json
import re
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
c = req("POST", "/api/v1/conversations", {"title": "deep research"})
cid = c["id"]
sent = req("POST", f"/api/v1/conversations/{cid}/messages",
           {"content": "Deeply research: what is SearXNG and how do you enable the JSON API? "
                       "Use web_research (or web_search plus web_fetch) to gather from at least "
                       "2 sources, then summarize with the source URLs. Do not ask questions.",
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
print("final answer:", final[:500].replace("\n", " | "))
urls = set(re.findall(r"https?://[^\s)\"']+", final))
print("cited urls:", len(urls))
ok = (status == "completed"
      and ("web_research" in names or ("web_search" in names and "web_fetch" in names))
      and len(urls) >= 2)
print("E2E RESULT:", "PASS" if ok else f"CHECK (status={status}, tools={names})")
