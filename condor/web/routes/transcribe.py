"""Voice transcription endpoint for the web dashboard."""

import logging

from fastapi import APIRouter, Depends, Form, UploadFile, File, HTTPException

from condor.web.auth import get_current_user
from condor.web.models import WebUser

log = logging.getLogger(__name__)
router = APIRouter(tags=["transcribe"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    model: str | None = Form(None),
    user: WebUser = Depends(get_current_user),
):
    """Transcribe an audio file to text using the local Whisper model.

    Optional form fields:
    - language: ISO code (e.g. "en", "es") or empty for auto-detect.
    - model: Whisper model size (tiny/base/small/medium/large-v3).
    """
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, "Audio file too large (max 10 MB)")

    if not data:
        raise HTTPException(400, "Empty audio file")

    # Resolve user voice preferences as fallback
    effective_lang = language
    effective_model = model
    if effective_lang is None or effective_model is None:
        try:
            from config_manager import get_config_manager
            cm = get_config_manager()
            voice_prefs = cm.get_user_preferences(user.id).get("voice", {})
            if effective_lang is None:
                effective_lang = voice_prefs.get("language")  # None = auto-detect
            if effective_model is None:
                effective_model = voice_prefs.get("whisper_model", "small")
        except Exception:
            pass

    effective_model = effective_model or "small"

    try:
        from utils.transcribe import transcribe_voice

        text = await transcribe_voice(
            data,
            language=effective_lang or None,
            model_size=effective_model,
        )
    except Exception as e:
        log.exception("Transcription failed")
        raise HTTPException(500, f"Transcription failed: {e}")

    if not text or not text.strip():
        return {"text": "", "error": "No speech detected"}

    return {"text": text.strip()}
