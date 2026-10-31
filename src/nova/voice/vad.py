"""Voice activity detection for hands-free mode.

WebRTC's VAD runs on fixed 10/20/30 ms frames of 16-bit mono PCM. The gate below
buffers whatever the browser sends, feeds whole frames to the detector, and
reports when an utterance has started and when it has ended.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nova.logging_setup import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 30
BYTES_PER_SAMPLE = 2
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * BYTES_PER_SAMPLE


@dataclass(slots=True)
class GateEvent:
    """What the gate observed after consuming some audio."""

    speech_started: bool = False
    speech_ended: bool = False
    speech_ms: int = 0
    silence_ms: int = 0


class SpeechGate:
    """Tracks speech and silence so hands-free turns end on their own."""

    def __init__(
        self,
        *,
        aggressiveness: int = 2,
        silence_ms: int = 900,
        min_speech_ms: int = 250,
    ) -> None:
        self._silence_limit_ms = silence_ms
        self._min_speech_ms = min_speech_ms
        self._buffer = bytearray()
        self._speech_ms = 0
        self._silence_ms = 0
        self._started = False
        self._finished = False
        self._detector: Any | None = None

        try:
            import webrtcvad

            self._detector = webrtcvad.Vad(aggressiveness)
        except (ImportError, ValueError) as exc:  # pragma: no cover - optional path
            log.warning("vad.unavailable", error=str(exc))
            self._detector = None

    @property
    def available(self) -> bool:
        return self._detector is not None

    @property
    def speech_ms(self) -> int:
        return self._speech_ms

    @property
    def finished(self) -> bool:
        return self._finished

    def reset(self) -> None:
        self._buffer.clear()
        self._speech_ms = 0
        self._silence_ms = 0
        self._started = False
        self._finished = False

    def feed(self, pcm: bytes) -> GateEvent:
        """Consume PCM and report state transitions."""
        event = GateEvent(speech_ms=self._speech_ms, silence_ms=self._silence_ms)
        if self._detector is None or self._finished:
            return event

        self._buffer.extend(pcm)
        while len(self._buffer) >= FRAME_BYTES:
            frame = bytes(self._buffer[:FRAME_BYTES])
            del self._buffer[:FRAME_BYTES]

            try:
                is_speech = self._detector.is_speech(frame, SAMPLE_RATE)
            except (ValueError, RuntimeError):  # pragma: no cover - defensive
                continue

            if is_speech:
                self._speech_ms += FRAME_MS
                self._silence_ms = 0
                if not self._started and self._speech_ms >= self._min_speech_ms:
                    self._started = True
                    event.speech_started = True
            else:
                if self._started:
                    self._silence_ms += FRAME_MS
                    if self._silence_ms >= self._silence_limit_ms:
                        self._finished = True
                        event.speech_ended = True
                        break
                elif self._speech_ms == 0:
                    # Leading silence; nothing to measure yet.
                    self._silence_ms += FRAME_MS

        event.speech_ms = self._speech_ms
        event.silence_ms = self._silence_ms
        return event
