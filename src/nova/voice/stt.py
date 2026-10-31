"""Speech to text with faster-whisper.

Audio arrives from the browser as 16 kHz mono 16-bit PCM. It is converted to a
float array in memory, transcribed, and discarded - nothing is written to disk.
The model is loaded lazily on the first transcription so startup stays quick.
"""

from __future__ import annotations

import asyncio
from threading import Lock
from typing import Any

import numpy as np

from nova.config import Settings
from nova.logging_setup import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16000
INT16_FULL_SCALE = 32768.0


class SttUnavailable(Exception):  # noqa: N818 - describes a state, not an error type
    """Raised when transcription cannot be performed."""


def pcm16_to_float32(pcm: bytes) -> np.ndarray[Any, Any]:
    """Convert little-endian 16-bit PCM bytes to a normalised float32 array."""
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    return samples / INT16_FULL_SCALE


class Transcriber:
    """Lazily loaded faster-whisper wrapper."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any | None = None
        self._lock = Lock()

    @property
    def available(self) -> bool:
        return self._settings.stt_enabled

    def _load(self) -> Any:
        """Load the model once, under a lock (called in a worker thread)."""
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover - dependency is pinned
                raise SttUnavailable("faster-whisper is not installed") from exc

            settings = self._settings
            settings.whisper_download_root.mkdir(parents=True, exist_ok=True)
            log.info("stt.loading", model=settings.whisper_model)
            self._model = WhisperModel(
                settings.whisper_model,
                device="cpu" if settings.whisper_device == "auto" else settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                download_root=str(settings.whisper_download_root),
            )
            log.info("stt.loaded", model=settings.whisper_model)
            return self._model

    async def preload(self) -> None:
        """Warm the model in the background so the first utterance is fast."""
        if not self.available:
            return
        try:
            await asyncio.to_thread(self._load)
        except (SttUnavailable, OSError, RuntimeError) as exc:
            log.warning("stt.preload_failed", error=str(exc))

    def _transcribe_sync(self, audio: np.ndarray[Any, Any]) -> str:
        model = self._load()
        settings = self._settings
        segments, _info = model.transcribe(
            audio,
            language=settings.whisper_language or None,
            beam_size=settings.whisper_beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    async def transcribe(self, pcm: bytes) -> str:
        """Transcribe raw PCM. Returns an empty string for silence."""
        if not self.available:
            raise SttUnavailable("speech to text is disabled in the settings")

        audio = pcm16_to_float32(pcm)
        duration = len(audio) / SAMPLE_RATE
        if duration < 0.2:
            return ""
        if duration > self._settings.max_audio_seconds:
            raise SttUnavailable(
                f"that clip is {duration:.0f}s long; "
                f"the limit is {self._settings.max_audio_seconds:.0f}s"
            )

        try:
            text = await asyncio.to_thread(self._transcribe_sync, audio)
        except SttUnavailable:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            log.warning("stt.failed", error=str(exc))
            raise SttUnavailable("the audio could not be transcribed") from exc

        log.info("stt.done", seconds=round(duration, 1), chars=len(text))
        return text
