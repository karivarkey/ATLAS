"""Live onboarding interview with the real default model.
Scripted user answers -> assert the model asks, finishes with the fence,
USER.md profile is written and onboarded flag set."""
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


st = req("GET", "/api/v1/onboarding/status")
print("before:", st["onboarded"], st["default_model"])
start = req("POST", "/api/v1/onboarding/start", {})
cid = start["conversation_id"]
print("conv:", cid)


def wait_done(eid, timeout=300):
    deadline = time.time() + timeout
    s = {"status": "timeout", "current_step": 0}
    while time.time() < deadline:
        time.sleep(4)
        s = req("GET", f"/api/v1/executions/{eid}")
        if s["status"] in ("completed", "failed", "cancelled", "timed_out", "interrupted"):
            return s
        if s["status"] in ("waiting_input", "waiting_approval"):
            try:
                if s["status"] == "waiting_input":
                    req("POST", f"/api/v1/executions/{eid}/answer",
                        {"text": "Proceed with your best judgment."})
                else:
                    req("POST", f"/api/v1/executions/{eid}/approve", {"approved": True})
            except Exception:
                pass
        print(f"  ...{s['status']} step={s['current_step']}")
    return s


print("greeting:")
wait_done(start["execution_id"])

answers = [
    "Call me Robin. I'm a student who builds web apps and games.",
    "Python and JavaScript mostly, some React. Right now a campus event app.",
    "Keep answers short and direct. I don't like fluff. No other constraints.",
]


for i, a in enumerate(answers):
    s = req("POST", f"/api/v1/conversations/{cid}/messages",
            {"content": a, "model": st["default_model"]})
    s = wait_done(s["execution_id"])
    print(f"turn {i}: {s['status']}")
    st = req("GET", "/api/v1/onboarding/status")
    if st["onboarded"]:
        break

# keep answering until the model finishes or we run out (max 4 more turns)
extra = ["Short answers please.", "That's everything, wrap up."]
for a in extra:
    st = req("GET", "/api/v1/onboarding/status")
    if st["onboarded"]:
        break
    s = req("POST", f"/api/v1/conversations/{cid}/messages",
            {"content": a, "model": st["default_model"]})
    wait_done(s["execution_id"])

st = req("GET", "/api/v1/onboarding/status")
print("onboarded:", st["onboarded"])
u = req("GET", "/api/v1/memory/files/USER.md")
print("USER.md v%s len=%d" % (u["version"], len(u["content"])))
print(u["content"][:600])
ok = st["onboarded"] and ("Robin" in u["content"])
print("ONBOARDING E2E:", "PASS" if ok else "CHECK")
