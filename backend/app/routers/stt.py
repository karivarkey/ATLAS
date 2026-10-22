from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..services import stt

router = APIRouter(prefix="/api/v1/stt", tags=["stt"])

ALLOWED_SUFFIXES = (".webm", ".mp3", ".wav", ".m4a", ".ogg", ".mp4")


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    name = (file.filename or "voice.webm").lower()
    if not name.endswith(ALLOWED_SUFFIXES):
        raise HTTPException(400, f"unsupported audio type: {file.filename}")
    audio = await file.read()
    try:
        result = await stt.transcribe(audio, file.filename or "voice.webm")
    except stt.STTError as exc:
        raise HTTPException(502, str(exc)[:500])
    return result


@router.get("/status")
def stt_status():
    from .. import config
    return {"configured": bool(config.ASSEMBLYAI_API_KEY),
            "model": ",".join(stt.SPEECH_MODELS), "language": stt.LANGUAGE}
