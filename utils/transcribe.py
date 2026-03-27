"""Voice message transcription using faster-whisper (open-source, runs locally)."""

import asyncio
import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# Lazy-loaded model instance
_model = None
_model_lock = asyncio.Lock()

MODEL_SIZE = "base"  # Options: tiny, base, small, medium, large-v3


def _get_model():
    """Load the Whisper model (CPU, int8 for speed)."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        log.info("Loading Whisper model '%s' ...", MODEL_SIZE)
        _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        log.info("Whisper model loaded.")
    return _model


async def transcribe_voice(file_bytes: bytes) -> str:
    """Transcribe voice message bytes (OGG/Opus) to text.

    Args:
        file_bytes: Raw audio bytes downloaded from Telegram.

    Returns:
        Transcribed text string.
    """
    async with _model_lock:
        # Write to temp file (faster-whisper needs a file path)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            text = await asyncio.to_thread(_transcribe_sync, tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    return text


def _transcribe_sync(audio_path: str) -> str:
    """Run transcription in a thread (blocking call)."""
    model = _get_model()
    segments, info = model.transcribe(audio_path, beam_size=5)
    text = " ".join(seg.text.strip() for seg in segments)
    log.info("Transcribed %.1fs audio → %d chars (lang=%s)", info.duration, len(text), info.language)
    return text
