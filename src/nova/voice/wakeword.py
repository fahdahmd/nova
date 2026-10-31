"""Optional wake-word detection with openWakeWord.

Off by default and behind an optional dependency, so a normal install stays
small. Install with ``uv pip install -e ".[wakeword]"`` and set
``NOVA_WAKEWORD_ENABLED=true``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from nova.config import Settings
from nova.logging_setup import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80 ms, the frame size openWakeWord expects


class WakeWordDetector:
    """Scores incoming audio against a bundled wake-word model."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any | None = None
        self._buffer = bytearray()
        self._enabled = settings.wakeword_enabled
        self._failed = False

    @property
    def enabled(self) -> bool:
        return self._enabled and not self._failed

    @property
    def available(self) -> bool:
        """True when the optional dependency is importable."""
        if not self._settings.wakeword_enabled:
            return False
        try:
            import openwakeword  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self) -> Any | None:
        if self._model is not None or self._failed:
            return self._model
        try:
            from openwakeword.model import Model

            self._model = Model(wakeword_models=[self._settings.wakeword_model])
            log.info("wakeword.loaded", model=self._settings.wakeword_model)
        except Exception as exc:  # openWakeWord raises broadly on missing models
            log.warning("wakeword.unavailable", error=str(exc))
            self._failed = True
            self._model = None
        return self._model

    def reset(self) -> None:
        self._buffer.clear()
        model = self._model
        if model is not None and hasattr(model, "reset"):
            model.reset()

    def feed(self, pcm: bytes) -> bool:
        """Return True once the wake word is heard."""
        if not self.enabled:
            return False
        model = self._load()
        if model is None:
            return False

        self._buffer.extend(pcm)
        frame_bytes = CHUNK_SAMPLES * 2
        detected = False

        while len(self._buffer) >= frame_bytes:
            frame = bytes(self._buffer[:frame_bytes])
            del self._buffer[:frame_bytes]
            samples = np.frombuffer(frame, dtype="<i2")
            try:
                scores = model.predict(samples)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("wakeword.predict_failed", error=str(exc))
                self._failed = True
                return False
            if any(score >= self._settings.wakeword_threshold for score in scores.values()):
                detected = True
                self.reset()
                break
        return detected
