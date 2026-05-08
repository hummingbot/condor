"""Voice transcription endpoint for the web dashboard."""

import logging

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException

from condor.web.auth import get_current_user
from condor.web.models import WebUser

log = logging.getLogger(__name__)
router = APIRouter(tags=["transcribe"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    user: WebUser = Depends(get_current_user),
):
    """Transcribe an audio file to text using the local Whisper model."""
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, "Audio file too large (max 10 MB)")

    if not data:
        raise HTTPException(400, "Empty audio file")

    try:
        from utils.transcribe import transcribe_voice

        text = await transcribe_voice(data)
    except Exception as e:
        log.exception("Transcription failed")
        raise HTTPException(500, f"Transcription failed: {e}")

    if not text or not text.strip():
        return {"text": "", "error": "No speech detected"}

    return {"text": text.strip()}
