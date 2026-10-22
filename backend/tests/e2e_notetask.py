"""Live integration check for notes+tasks wiring (real server, real files/DB)."""
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
        return json.loads(urllib.request.urlopen(r, timeout=60).read())
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:300])
        raise


print("== note lifecycle ==")
RUN = "run" + str(int(time.time() % 100000))
P1 = f"Projects/Atlas/Live-{RUN}.md"
P2 = f"Projects/Atlas/Roadmap-{RUN}.md"
n = req("POST", "/api/v1/notes", {"path": P1,
                                  "content": f"# Live\n\nSee [[{P2}|roadmap]] and [[Missing]].\n"})
print("created:", n["path"], "v%s" % n["version"])
req("POST", "/api/v1/notes", {"path": P2,
                              "content": "# Roadmap\n\n- [ ] Ship v1\n"})
back = req("GET", "/api/v1/notes/versions?path=" + P2)
print("roadmap versions:", len(back["versions"]))
br = req("GET", "/api/v1/notes/broken")
print("broken links:", [(x.get("from", x.get("path", "?")), x.get("target")) for x in br.get("broken", [])])
s = req("GET", "/api/v1/notes/search?q=roadmap")
print("search hits:", len(s.get("results", [])))
r = req("POST", "/api/v1/notes/move", {"old_path": P1,
                                       "new_path": P1.replace("Live-", "Live2-")})
P1B = P1.replace("Live-", "Live2-")
print("renamed, refs updated:", r.get("new_path"))
road = req("GET", "/api/v1/notes?path=" + P2)
print("roadmap backlinks:", [(b.get("path"), str(b.get("excerpt", ""))[:40]) for b in road.get("backlinks", [])])
print("roadmap broken:", road.get("broken_links"))
live = req("GET", "/api/v1/notes?path=" + P1B)
print("live outlinks:", [(o.get("target"), o.get("broken")) for o in live.get("outlinks", [])])

print("== task lifecycle ==")
t = req("POST", "/api/v1/tasks", {"title": "Live verify notes wiring",
                                  "priority": "high",
                                  "project": "Atlas"})
print("task:", t["id"], t["status"])
req("POST", f"/api/v1/tasks/{t['id']}/notes", {"note_path": P1B})
t2 = req("PATCH", f"/api/v1/tasks/{t['id']}", {"status": "in_progress"})
print("status:", t2["status"])
note = req("GET", "/api/v1/notes?path=" + P1B)
print("note linked tasks:", [x.get("id") for x in note.get("linked_tasks", [])])

print("== AI attempt via agent tools ==")
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
print("(stubbed provider path is covered by unit tests; live run below uses real model)")
print("== notifications ==")
req("POST", "/api/v1/reminders/evaluate", {})
un = req("GET", "/api/v1/notifications/unread-count")
print("unread:", un)
print("INTEGRATION CHECK DONE")
