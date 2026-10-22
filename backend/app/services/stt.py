"""Speech-to-text via AssemblyAI (pre-recorded API, cheapest `nano` model,
English-only per user requirement).

Flow: upload bytes → POST /v2/transcript {audio_url, speech_model, language_code}
→ poll GET /v2/transcript/{id} until completed/error. Key stays server-side.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .. import config

log = logging.getLogger("atlas.stt")

BASE = "https://api.assemblyai.com"
# universal-2: the cost-effective model (nano/speech_model are deprecated).
# English-only per user requirement.
SPEECH_MODELS = ["universal-2"]
LANGUAGE = "en"
MAX_BYTES = 25 * 1024 * 1024
POLL_TIMEOUT = 300


def _headers(extra: dict | None = None) -> dict:
    h = {"authorization": config.ASSEMBLYAI_API_KEY}
    if extra:
        h.update(extra)
    return h


class STTError(RuntimeError):
    pass


async def transcribe(audio: bytes, filename: str = "voice.webm") -> dict:
    if not config.ASSEMBLYAI_API_KEY:
        raise STTError("transcription is not configured (ASSEMBLYAI_API_KEY missing)")
    if not audio or len(audio) > MAX_BYTES:
        raise STTError(f"audio must be 1 byte–{MAX_BYTES // 1024 // 1024}MB")
    async with httpx.AsyncClient(timeout=60) as client:
        up = await client.post(f"{BASE}/v2/upload", headers=_headers(),
                               content=audio)
        if up.status_code >= 400:
            raise STTError(f"upload failed: HTTP {up.status_code}")
        audio_url = up.json().get("upload_url", "")
        if not audio_url:
            raise STTError("upload failed: no upload_url returned")
        sub = await client.post(
            f"{BASE}/v2/transcript",
            headers=_headers({"content-type": "application/json"}),
            json={"audio_url": audio_url, "speech_models": SPEECH_MODELS,
                  "language_code": LANGUAGE},
        )
        if sub.status_code >= 400:
            body = sub.text[:300]
            raise STTError(f"transcription request failed: HTTP {sub.status_code}: {body}")
        tid = sub.json().get("id", "")
        if not tid:
            raise STTError("transcription request failed: no id returned")
        start = time.time()
        while time.time() - start < POLL_TIMEOUT:
            await asyncio.sleep(2)
            poll = await client.get(f"{BASE}/v2/transcript/{tid}", headers=_headers())
            if poll.status_code >= 400:
                raise STTError(f"poll failed: HTTP {poll.status_code}")
            data = poll.json()
            status = data.get("status")
            if status == "completed":
                return {"text": data.get("text", "") or "",
                        "model": ",".join(SPEECH_MODELS), "language": LANGUAGE,
                        "speech_model_used": data.get("speech_model_used", ""),
                        "audio_duration": data.get("audio_duration"),
                        "transcript_id": tid}
            if status == "error":
                raise STTError(f"transcription failed: {data.get('error', 'unknown')}"[:500])
        raise STTError("transcription timed out")
