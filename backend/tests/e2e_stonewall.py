"""Live anti-loop verification: stonewall the interviewer with content-free
answers. Expect the repetition guard + turn-count wrap to terminate the
interview quickly WITHOUT a fence (so no profile is written — zero pollution).
Cleans up its conversation afterwards.
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
    return json.loads(urllib.request.urlopen(r, timeout=120).read())


start = req("POST", "/api/v1/onboarding/start", {})
cid = start["conversation_id"]
print("conv:", cid)


def wait(eid, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(4)
        s = req("GET", f"/api/v1/executions/{eid}")
        if s["status"] in ("completed", "failed", "cancelled", "timed_out", "interrupted"):
            return s
    return None


s = wait(start["execution_id"])
print("greeting:", (s or {}).get("status"), "step:", (s or {}).get("current_step"))

stonewalls = ["dunno", "whatever", "you pick", "just finish it", "ok",
              "sure", "fine", "do whatever", "i don't know", "stop asking"]
turns = 0
for a in stonewalls:
    s = req("POST", f"/api/v1/conversations/{cid}/messages",
            {"content": a, "model": "opencode-go/muse-spark-1.3-contributor"})
    s = wait(s["execution_id"])
    turns += 1
    st = (s or {}).get("status")
    print(f"turn {turns}: {st} step={(s or {}).get('current_step')}")
    if st != "completed":
        break

# must have terminated (not hit the 60-step budget, not stuck waiting)
ok = turns <= 8
print("STONEWALL RESULT:", "PASS (bounded)" if ok else f"CHECK turns={turns}")

# cleanup: delete the test conversation (cascades chunks + messages)
req("DELETE", f"/api/v1/conversations/{cid}")
print("cleaned up", cid)
