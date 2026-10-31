"""Voice helpers: speech text preparation, PCM handling and the silence gate."""

from __future__ import annotations

import math
import struct

import pytest

from nova.voice.stt import pcm16_to_float32
from nova.voice.tts import clean_for_speech, split_for_speech
from nova.voice.vad import FRAME_BYTES, SpeechGate


# ------------------------------------------------------------ speech text
def test_markup_is_stripped_before_speaking() -> None:
    spoken = clean_for_speech("**Bold** and `code` and [a link](http://example.com).")
    assert spoken == "Bold and code and a link."


def test_code_blocks_are_summarised_not_read_out() -> None:
    spoken = clean_for_speech("Here:\n```python\nprint('hi')\n```\nDone.")
    assert "print" not in spoken
    assert "code block" in spoken


def test_list_markers_and_headings_are_removed() -> None:
    spoken = clean_for_speech("## Heading\n- first\n- second")
    assert spoken == "Heading first second"


def test_text_is_split_on_sentence_boundaries() -> None:
    """Sentences are packed up to the limit, and never cut mid-sentence."""
    pieces = split_for_speech("One. Two. Three.", 10)
    assert pieces == ["One. Two.", "Three."]
    assert all(len(piece) <= 10 for piece in pieces)

    tight = split_for_speech("One. Two. Three.", 6)
    assert tight == ["One.", "Two.", "Three."]


def test_short_text_stays_in_one_piece() -> None:
    assert split_for_speech("Just one sentence here.", 180) == ["Just one sentence here."]


def test_a_very_long_sentence_is_broken_up() -> None:
    sentence = " ".join(["word"] * 120)
    pieces = split_for_speech(sentence, 60)
    assert len(pieces) > 1
    assert all(len(piece) <= 60 for piece in pieces)
    assert " ".join(pieces).split() == sentence.split()


def test_empty_text_produces_nothing_to_say() -> None:
    assert split_for_speech("", 180) == []
    assert split_for_speech("   \n  ", 180) == []


# -------------------------------------------------------------------- pcm
def test_pcm_is_normalised_to_the_float_range() -> None:
    pcm = struct.pack("<4h", 0, 32767, -32768, 16384)
    samples = pcm16_to_float32(pcm)
    assert samples[0] == pytest.approx(0.0)
    assert samples[1] == pytest.approx(1.0, abs=1e-4)
    assert samples[2] == pytest.approx(-1.0)
    assert samples[3] == pytest.approx(0.5)


def test_a_trailing_odd_byte_is_discarded() -> None:
    assert len(pcm16_to_float32(struct.pack("<2h", 1, 2) + b"\x00")) == 2


def test_empty_audio_is_handled() -> None:
    assert len(pcm16_to_float32(b"")) == 0


# -------------------------------------------------------------------- vad
def tone(frames: int, amplitude: int = 12000) -> bytes:
    """A 300 Hz tone at 16 kHz, which the detector reads as speech."""
    samples = []
    for index in range(frames * (FRAME_BYTES // 2)):
        samples.append(int(amplitude * math.sin(index * 2 * math.pi * 300 / 16000)))
    return struct.pack(f"<{len(samples)}h", *samples)


def silence(frames: int) -> bytes:
    return b"\x00" * (frames * FRAME_BYTES)


def test_the_gate_reports_when_speech_starts() -> None:
    gate = SpeechGate(aggressiveness=1, silence_ms=300, min_speech_ms=90)
    if not gate.available:  # pragma: no cover - webrtcvad is a pinned dependency
        pytest.skip("webrtcvad is unavailable")

    event = gate.feed(tone(10))
    assert event.speech_started is True
    assert event.speech_ms >= 90
    assert gate.finished is False


def test_the_gate_closes_after_enough_silence() -> None:
    gate = SpeechGate(aggressiveness=1, silence_ms=300, min_speech_ms=90)
    if not gate.available:  # pragma: no cover
        pytest.skip("webrtcvad is unavailable")

    gate.feed(tone(10))
    event = gate.feed(silence(20))
    assert event.speech_ended is True
    assert gate.finished is True


def test_leading_silence_alone_never_ends_a_turn() -> None:
    gate = SpeechGate(aggressiveness=1, silence_ms=300, min_speech_ms=90)
    if not gate.available:  # pragma: no cover
        pytest.skip("webrtcvad is unavailable")

    event = gate.feed(silence(40))
    assert event.speech_started is False
    assert event.speech_ended is False
    assert gate.finished is False


def test_resetting_the_gate_clears_its_state() -> None:
    gate = SpeechGate(aggressiveness=1, silence_ms=300, min_speech_ms=90)
    if not gate.available:  # pragma: no cover
        pytest.skip("webrtcvad is unavailable")

    gate.feed(tone(10))
    gate.feed(silence(20))
    assert gate.finished is True

    gate.reset()
    assert gate.finished is False
    assert gate.speech_ms == 0


def test_partial_frames_are_buffered_until_complete() -> None:
    gate = SpeechGate(aggressiveness=1, silence_ms=300, min_speech_ms=90)
    if not gate.available:  # pragma: no cover
        pytest.skip("webrtcvad is unavailable")

    audio = tone(10)
    for offset in range(0, len(audio), 100):  # deliberately not frame-aligned
        gate.feed(audio[offset : offset + 100])
    assert gate.speech_ms >= 90
