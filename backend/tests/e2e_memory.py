"""Live memory proof on the running server:
1. memory health (indexer backlog drains via real Ollama embeddings)
2. cross-chat lexical search over the user's real history
3. manual dream trigger (real reasoning model) -> records + USER.md"""
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


print("== health ==")
h = req("GET", "/api/v1/memory/health")
print("embed:", h["embeddings"]["model"], h["embeddings"]["ok"],
      h["embeddings"].get("last_error", ""))
print("index:", h["indexing"])
print("SOUL v%s USER v%s" % (h["soul_version"], h["user_version"]))

time.sleep(20)  # let the indexer drain with real embeddings
h = req("GET", "/api/v1/memory/health")
print("after drain:", h["indexing"], "embed_ok:", h["embeddings"]["ok"])

print("== cross-chat search ==")
s = req("GET", "/api/v1/memory/search?q=" + "muse-spark+system+stats")
print("semantic:", s["semantic"], "| hits:", len(s["results"]))
for hit in s["results"][:3]:
    print("-", hit["match"], hit["score"], hit["conversation_id"],
          str(hit["text"])[:120].replace("\n", " | "))

print("== dream ==")
d = req("POST", "/api/v1/memory/dream", {"trigger": "manual"})
print("job:", d["job_id"], d["status"])
for _ in range(60):
    time.sleep(5)
    j = req("GET", f"/api/v1/memory/jobs/{d['job_id']}")
    if j["status"] not in ("queued", "collecting", "analyzing", "proposing",
                           "validating", "applying"):
        break
    print("dream:", j["status"])
print("final:", j["status"], j.get("stats_json"))
recs = req("GET", "/api/v1/memory/records?status=active")
print("active records:", len(recs["records"]))
for r in recs["records"][:6]:
    print("-", r["memory_type"], "|", r["subject"], "|", r["predicate"], "|",
          r["object_value"][:60])
u = req("GET", "/api/v1/memory/files/USER.md")
print("USER.md v%s len=%d" % (u["version"], len(u["content"])))
print("MEMORY LIVE:", "PASS" if (j["status"] == "completed" and recs["records"]) else "CHECK")
