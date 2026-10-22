"""Verify live OpenCode Go catalog with the Hermes-sourced key.

Prints: key source, key_configured, catalog source/stale/error, model count,
first model ids. NEVER prints the key itself.
"""
import json
import urllib.request

B = "http://127.0.0.1:8765"


def get(p):
    return json.loads(urllib.request.urlopen(B + p, timeout=60).read())


print("health:", get("/api/v1/health"))
print("settings default:", get("/api/v1/settings").get("default_model"))
cat = get("/api/v1/models?refresh=true")
print("catalog source:", cat["source"], "| stale:", cat["stale"])
print("catalog error:", (cat["error"] or "(none)")[:200])
print("model count:", len(cat["models"]))
for m in cat["models"][:15]:
    print(" -", m["id"], "|", m["protocol"], "|", m["endpoint"])
print("has ollama/qwen3:0.6b:", any(m["id"] == "ollama/qwen3:0.6b" for m in cat["models"]))
