import json
import urllib.request

B = "http://127.0.0.1:8765"


def get(p):
    return json.loads(urllib.request.urlopen(B + p, timeout=30).read())


def post(p, body=None):
    r = urllib.request.Request(
        B + p,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(r, timeout=30).read())


print("models:", json.dumps(get("/api/v1/models"))[:400])
c = post("/api/v1/conversations", {"title": "smoke"})
print("conv:", c["id"], c["title"])
print("msgs:", get(f"/api/v1/conversations/{c['id']}/messages"))
print("ws files:", json.dumps(get(f"/api/v1/workspaces/{c['id']}/files"))[:200])
print("settings key_configured:", get("/api/v1/settings")["key_configured"])
print("SMOKE OK")
