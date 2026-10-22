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


# Exact per-model routing from the official Go docs
# (https://opencode.ai/docs/go/#endpoints). Match longest id first so
# e.g. minimax-m3.1-flash (chat) is not caught by minimax-m3 (messages).
# Unknown ids fall back to the chat_completions heuristic.
GO_BASE = "https://opencode.ai/inference/go"
_RESPONSE_IDS = {
    "grok-4.7", "grok-4.6",
    "gpt-6-luna", "gpt-5.6-luna",
    "muse-spark-1.3-contributor", "muse-spark-1.2-contributor",
}
_MESSAGE_IDS = {
    "claude-haiku-5-5",
    "minimax-m3",
    "qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus",
}
_GOOGLE_IDS = {
    "gemini-4-argon-contributor",
}


def _protocol_for(model_id: str, raw: dict) -> tuple[str, str]:
    mid = model_id.split("/", 1)[-1] if "/" in model_id else model_id
    if mid in _RESPONSE_IDS or mid.startswith("grok-") or mid.startswith("muse-spark-"):
        return "responses", f"{GO_BASE}/openai/v1/responses"
    if mid in _GOOGLE_IDS or mid.startswith("gemini-"):
        return "google", f"{GO_BASE}/google/v1beta/models/{mid}"
    if mid in _MESSAGE_IDS or mid.startswith("claude-") or mid.startswith("qwen3."):
        return "messages", f"{GO_BASE}/anthropic/v1/messages"
    explicit = str(raw.get("protocol", "") or raw.get("api", "")).lower()
    if "response" in explicit:
        return "responses", f"{GO_BASE}/openai/v1/responses"
    if "message" in explicit or "anthropic" in explicit:
        return "messages", f"{GO_BASE}/anthropic/v1/messages"
    if "google" in explicit or "gemini" in explicit:
        return "google", f"{GO_BASE}/google/v1beta/models/{mid}"
    return "chat_completions", f"{GO_BASE}/openai/v1/chat/completions"


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


def key_configured() -> bool:
    return bool(config.OPENCODE_ZEN_API_KEY)


async def get_catalog(force_refresh: bool = False) -> dict:
    """Chat model catalog: OpenCode Go live models only.

    Ollama is embeddings-only in this setup and is deliberately NOT listed
    here (no embedding implementation yet). This catalog drives the chat
    model picker, so every entry must be a chat-capable model.
    """
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

    ollama_models: list[dict] = []  # reserved for future embedding support; not chat
    if live:
        _write_cache(live)
        fetched_at = int(time.time())
        return {"models": live,
                "fetched_at": fetched_at, "stale": False, "error": "", "source": "live"}
    if cached:
        return {"models": cached, "fetched_at": fetched_at, "stale": True,
                "error": error, "source": "cache"}
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
