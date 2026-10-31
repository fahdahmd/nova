"""Voice pipeline: transcription, synthesis, VAD and the optional wake word."""

from __future__ import annotations

from nova.voice.stt import SttUnavailable, Transcriber, pcm16_to_float32
from nova.voice.tts import (
    AudioFrame,
    Speaker,
    TtsUnavailable,
    clean_for_speech,
    split_for_speech,
)
from nova.voice.vad import SpeechGate
from nova.voice.wakeword import WakeWordDetector

__all__ = [
    "AudioFrame",
    "Speaker",
    "SpeechGate",
    "SttUnavailable",
    "Transcriber",
    "TtsUnavailable",
    "WakeWordDetector",
    "clean_for_speech",
    "pcm16_to_float32",
    "split_for_speech",
]
