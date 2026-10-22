"""Embedding provider abstraction. Default: local Ollama qwen3-embedding:0.6b
(1024-dim, verified). Batched, retries with backoff, dim-validated, normalized
in storage layer. Never fabricates vectors."""
from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

log = logging.getLogger("atlas.embed")

MODEL = os.environ.get("ATLAS_EMBED_MODEL", "qwen3-embedding:0.6b")
BASE_URL = os.environ.get("ATLAS_EMBED_URL", "http://localhost:11434/api/embed")
DIM = int(os.environ.get("ATLAS_EMBED_DIM", "1024"))
BATCH = int(os.environ.get("ATLAS_EMBED_BATCH", "16"))

_last_ok: float = 0.0
_last_error: str = ""
_last_latency_ms: float = 0.0


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Returns one normalized float vector per input. Raises on failure."""
    global _last_ok, _last_error, _last_latency_ms
    texts = [(t or "")[:4000] for t in texts]
    if not texts:
        return []
    delay = 1.0
    last: Exception | None = None
    for attempt in range(4):
        try:
            start = time.time()
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(BASE_URL, json={"model": MODEL, "input": texts})
                resp.raise_for_status()
                data = resp.json()
            vecs = data.get("embeddings", [])
            if len(vecs) != len(texts):
                raise ValueError(f"expected {len(texts)} vectors, got {len(vecs)}")
            for v in vecs:
                if len(v) != DIM:
                    raise ValueError(f"dim mismatch: got {len(v)}, want {DIM}")
            _last_ok, _last_latency_ms, _last_error = time.time(), (time.time() - start) * 1000, ""
            return [list(map(float, v)) for v in vecs]
        except Exception as exc:
            last = exc
            _last_error = f"{type(exc).__name__}: {exc}"[:300]
            log.info("embed attempt %d failed: %s", attempt + 1, _last_error)
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError(f"embedding failed after retries: {last}")


def health() -> dict:
    return {"ok": _last_error == "" or (time.time() - _last_ok) < 3600,
            "model": MODEL, "dim": DIM, "url": BASE_URL,
            "last_ok": _last_ok, "last_latency_ms": round(_last_latency_ms, 1),
            "last_error": _last_error}
