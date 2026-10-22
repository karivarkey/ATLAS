"""OpenCode Go model catalog service.

Uses the documented endpoint ``GET {base}/models`` with Bearer auth when
``OPENCODE_ZEN_API_KEY`` is configured. Responses are normalized, cached
in SQLite for the configured TTL, and served stale-with-warning when the
provider is unreachable. Never invents model identifiers.
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from .. import config, db

log = logging.getLogger("atlas.catalog")

FALLBACK_NOTE = (
    "OpenCode Go catalog unreachable and no cache available. "
    "Configure OPENCODE_ZEN_API_KEY to enable live discovery."
)


def _provider_of(raw: dict) -> str:
    for key in ("provider", "vendor", "owner", "family"):
        if raw.get(key):
            return str(raw[key])
    model_id = str(raw.get("id", ""))
    if "/" in model_id:
        return model_id.split("/")[0]
    return "opencode-go"


def _protocol_for(model_id: str, raw: dict) -> tuple[str, str]:
    """Return (protocol, endpoint-path) without hardcoding one for all models."""
    explicit = str(raw.get("protocol", "") or raw.get("api", "")).lower()
    base = config.OPENCODE_GO_BASE
    if "response" in explicit:
        return "responses", f"{base}/responses"
    if "message" in explicit or "anthropic" in explicit:
        return "messages", f"{base}/messages"
    if "chat" in explicit or "openai" in explicit:
        return "chat_completions", f"{base}/chat/completions"
    mid = model_id.lower()
    if "claude" in mid or "anthropic" in mid:
        return "messages", f"{base}/messages"
    # Default family used by OpenCode Go for open coding models.
    return "chat_completions", f"{base}/chat/completions"


def normalize(raw_models: list[dict]) -> list[dict]:
    out = []
    for raw in raw_models:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        model_id = str(raw["id"])
        protocol, endpoint = _protocol_for(model_id, raw)
        out.append(
            {
                "id": f"opencode-go/{model_id}" if "/" not in model_id else model_id,
                "name": str(raw.get("name") or raw.get("display_name") or model_id),
                "provider": _provider_of(raw),
                "protocol": protocol,
                "endpoint": endpoint,
                "context_window": raw.get("context_window") or raw.get("context"),
                "supports_tools": bool(raw.get("supports_tools", True)),
            }
        )
    # De-duplicate by id, keep first.
    seen: dict[str, dict] = {}
    for m in out:
        seen.setdefault(m["id"], m)
    return sorted(seen.values(), key=lambda m: m["id"])


def _read_cache() -> tuple[list[dict], int]:
    row = db.query_one("SELECT payload_json, fetched_at FROM model_cache WHERE id = 1")
    if not row:
        return [], 0
    try:
        return json.loads(row["payload_json"] or "[]"), int(row["fetched_at"])
    except Exception:
        return [], 0


def _write_cache(models: list[dict]) -> None:
    db.execute(
        "INSERT INTO model_cache (id, payload_json, fetched_at) VALUES (1, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json,"
        " fetched_at=excluded.fetched_at",
        (json.dumps(models), int(time.time())),
    )


async def _fetch_live() -> list[dict]:
    headers = {}
    if config.OPENCODE_ZEN_API_KEY:
        headers["Authorization"] = f"Bearer {config.OPENCODE_ZEN_API_KEY}"
    async with httpx.AsyncClient(timeout=config.CATALOG_TIMEOUT_SECONDS) as client:
        resp = await client.get(config.OPENCODE_GO_MODELS_URL, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict):
        for key in ("models", "data", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError(f"unexpected catalog shape: {type(data).__name__}")
    return normalize(data)


async def _fetch_ollama() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.OLLAMA_BASE}/api/tags")
            resp.raise_for_status()
            data = resp.json()
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            if name:
                models.append(
                    {
                        "id": f"ollama/{name}",
                        "name": name,
                        "provider": "ollama",
                        "protocol": "ollama",
                        "endpoint": f"{config.OLLAMA_BASE}/api/chat",
                        "context_window": None,
                        "supports_tools": True,
                    }
                )
        return models
    except Exception as exc:
        log.info("ollama unavailable: %s", exc)
        return []


def key_configured() -> bool:
    return bool(config.OPENCODE_ZEN_API_KEY)


async def get_catalog(force_refresh: bool = False) -> dict:
    cached, fetched_at = _read_cache()
    ttl = int(_setting("catalog_ttl_seconds", config.CATALOG_TTL_SECONDS))
    fresh = cached and (time.time() - fetched_at) < ttl
    if fresh and not force_refresh:
        return {"models": cached, "fetched_at": fetched_at, "stale": False,
                "error": "", "source": "cache"}

    error = ""
    live: list[dict] = []
    if key_configured():
        try:
            live = await _fetch_live()
            fetched_at = int(time.time())
        except Exception as exc:  # provider outage / auth / rate limit
            error = f"{type(exc).__name__}: {exc}"
            log.warning("catalog fetch failed: %s", error)
    else:
        error = "OPENCODE_ZEN_API_KEY is not configured"

    ollama_models = await _fetch_ollama()
    if live:
        merged: dict[str, dict] = {m["id"]: m for m in live}
        for m in ollama_models:
            merged.setdefault(m["id"], m)
        merged_list = sorted(merged.values(), key=lambda m: m["id"])
        _write_cache(merged_list)  # cache the merged view, not just live
        fetched_at = int(time.time())
        return {"models": merged_list,
                "fetched_at": fetched_at, "stale": False, "error": "", "source": "live"}
    if cached:
        return {"models": cached, "fetched_at": fetched_at, "stale": True,
                "error": error, "source": "cache"}
    if ollama_models:
        return {"models": ollama_models, "fetched_at": int(time.time()),
                "stale": True, "error": error or FALLBACK_NOTE, "source": "ollama"}
    return {"models": [], "fetched_at": 0, "stale": True,
            "error": error or FALLBACK_NOTE, "source": "fallback"}


async def validate_model(model_id: str) -> tuple[bool, dict]:
    """Check a requested model against the catalog. Returns (ok, model)."""
    if not model_id:
        return False, {}
    catalog = await get_catalog()
    for m in catalog["models"]:
        if m["id"] == model_id:
            return True, m
    return False, {}


def _setting(key: str, default):
    row = db.query_one("SELECT value_json FROM settings WHERE key = ?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except Exception:
        return default
