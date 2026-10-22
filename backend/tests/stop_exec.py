import json
import sys
import urllib.request

B = "http://127.0.0.1:8765"
eid = sys.argv[1]


def req(method, path, body=None):
    r = urllib.request.Request(
        B + path,
        data=json.dumps(body or {}).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    return json.loads(urllib.request.urlopen(r, timeout=60).read())


snap = req("GET", f"/api/v1/executions/{eid}")
print(snap["status"], "step", snap["current_step"], (snap.get("error") or "")[:200])
if snap["status"] not in ("completed", "failed", "cancelled", "timed_out", "interrupted"):
    print(req("POST", f"/api/v1/executions/{eid}/stop"))
    print("stopped")
evs = req("GET", f"/api/v1/executions/{eid}/events")["events"]
from collections import Counter
print(Counter(e["type"] for e in evs))
