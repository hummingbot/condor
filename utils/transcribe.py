"""Voice message transcription using faster-whisper (open-source, runs locally)."""

import asyncio
import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# Lazy-loaded model instances keyed by model size
_models: dict[str, object] = {}
_model_lock = asyncio.Lock()

VALID_MODELS = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_MODEL = "small"


def _get_model(model_size: str = DEFAULT_MODEL):
    """Load the Whisper model (CPU, int8 for speed). Cached per size."""
    if model_size not in VALID_MODELS:
        model_size = DEFAULT_MODEL

    if model_size not in _models:
        from faster_whisper import WhisperModel

        log.info("Loading Whisper model '%s' ...", model_size)
        _models[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
        log.info("Whisper model loaded: %s", model_size)
    return _models[model_size]


async def transcribe_voice(
    file_bytes: bytes,
    language: str | None = None,
    model_size: str = DEFAULT_MODEL,
) -> str:
    """Transcribe voice message bytes (OGG/Opus) to text.

    Args:
        file_bytes: Raw audio bytes downloaded from Telegram.
        language: ISO language code (e.g. "en", "es") or None for auto-detect.
        model_size: Whisper model size to use.

    Returns:
        Transcribed text string.
    """
    async with _model_lock:
        # Write to temp file (faster-whisper needs a file path)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            text = await asyncio.to_thread(
                _transcribe_sync, tmp_path, language, model_size
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    return text


def _transcribe_sync(
    audio_path: str,
    language: str | None = None,
    model_size: str = DEFAULT_MODEL,
) -> str:
    """Run transcription in a thread (blocking call)."""
    model = _get_model(model_size)
    kwargs: dict = {"beam_size": 5}
    if language:
        kwargs["language"] = language
    segments, info = model.transcribe(audio_path, **kwargs)
    text = " ".join(seg.text.strip() for seg in segments)
    log.info(
        "Transcribed %.1fs audio → %d chars (lang=%s, model=%s)",
        info.duration, len(text), info.language, model_size,
    )
    return text
