"""Text to speech with Piper.

Speech is produced sentence by sentence and streamed to the browser as raw
16-bit PCM chunks, so playback starts while the rest is still being
synthesised. Generation is cancellable, which is what makes barge-in feel
instant.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from threading import Lock
from typing import Any

from nova.config import Settings
from nova.logging_setup import get_logger

log = get_logger(__name__)

# Split on sentence ends, keeping the punctuation with the sentence.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:])\s+")
# Markdown decorations that should not be read aloud.
_SPEECH_CLEANUP: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"```.*?```", re.DOTALL), " code block "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\*\*([^*]*)\*\*"), r"\1"),
    (re.compile(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)"), r"\1"),
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),
    (re.compile(r"^\s*#+\s*", re.MULTILINE), ""),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"<[^>]+>"), " "),
    (re.compile(r"\s+"), " "),
)


class TtsUnavailable(Exception):  # noqa: N818 - describes a state, not an error type
    """Raised when speech synthesis is not possible."""


@dataclass(slots=True)
class AudioFrame:
    """One chunk of synthesised speech."""

    pcm: bytes
    sample_rate: int


def clean_for_speech(text: str) -> str:
    """Strip markup so the voice reads prose, not punctuation soup."""
    cleaned = text
    for pattern, replacement in _SPEECH_CLEANUP:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned.strip()


def split_for_speech(text: str, max_chars: int) -> list[str]:
    """Break text into speakable pieces, preferring sentence boundaries."""
    cleaned = clean_for_speech(text)
    if not cleaned:
        return []

    pieces: list[str] = []
    buffer = ""
    for sentence in _SENTENCE_SPLIT.split(cleaned):
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            pieces.append(buffer)
        # A single very long sentence still has to be broken somewhere.
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        buffer = sentence
    if buffer:
        pieces.append(buffer)
    return [piece for piece in pieces if piece]


class Speaker:
    """Lazily loaded Piper voice."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._voice: Any | None = None
        self._config: Any | None = None
        self._lock = Lock()
        self._sample_rate = 22050

    # ------------------------------------------------------------ availability
    @property
    def available(self) -> bool:
        """True when TTS is enabled and the voice model is present."""
        return self._settings.tts_enabled and self._settings.piper_voice_path.exists()

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def describe(self) -> str:
        return self._settings.piper_voice_path.stem

    # ------------------------------------------------------------------ loading
    def _load(self) -> Any:
        with self._lock:
            if self._voice is not None:
                return self._voice

            settings = self._settings
            if not settings.tts_enabled:
                raise TtsUnavailable("text to speech is disabled in the settings")
            path = settings.piper_voice_path
            if not path.exists():
                raise TtsUnavailable(
                    f"the voice model {path.name} is missing; "
                    "run `python scripts/download_voice.py`"
                )

            try:
                from piper import PiperVoice, SynthesisConfig
            except ImportError as exc:  # pragma: no cover - dependency is pinned
                raise TtsUnavailable("piper-tts is not installed") from exc

            config_path = path.with_suffix(path.suffix + ".json")
            log.info("tts.loading", voice=path.name)
            self._voice = PiperVoice.load(
                str(path), config_path=str(config_path) if config_path.exists() else None
            )
            self._config = SynthesisConfig(
                length_scale=settings.piper_length_scale,
                noise_scale=settings.piper_noise_scale,
                noise_w_scale=settings.piper_noise_w,
                normalize_audio=True,
            )
            self._sample_rate = int(getattr(self._voice.config, "sample_rate", 22050))
            log.info("tts.loaded", voice=path.name, sample_rate=self._sample_rate)
            return self._voice

    async def preload(self) -> None:
        if not self.available:
            return
        try:
            await asyncio.to_thread(self._load)
        except (TtsUnavailable, OSError, RuntimeError) as exc:
            log.warning("tts.preload_failed", error=str(exc))

    # -------------------------------------------------------------- synthesis
    def _synthesise_sync(self, text: str) -> list[AudioFrame]:
        voice = self._load()
        frames: list[AudioFrame] = []
        chunks: Iterator[Any] = voice.synthesize(text, syn_config=self._config)
        for chunk in chunks:
            pcm = bytes(chunk.audio_int16_bytes)
            if pcm:
                frames.append(AudioFrame(pcm=pcm, sample_rate=int(chunk.sample_rate)))
        return frames

    async def stream(
        self, text: str, *, cancelled: asyncio.Event | None = None
    ) -> AsyncIterator[AudioFrame]:
        """Yield audio for ``text``, stopping promptly if cancelled."""
        if not self._settings.tts_enabled:
            raise TtsUnavailable("text to speech is disabled in the settings")

        for piece in split_for_speech(text, self._settings.tts_chunk_chars):
            if cancelled is not None and cancelled.is_set():
                return
            try:
                frames = await asyncio.to_thread(self._synthesise_sync, piece)
            except TtsUnavailable:
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                log.warning("tts.failed", error=str(exc))
                raise TtsUnavailable("speech synthesis failed") from exc

            for frame in frames:
                if cancelled is not None and cancelled.is_set():
                    return
                self._sample_rate = frame.sample_rate
                yield frame
