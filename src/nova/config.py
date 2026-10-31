"""Typed application configuration.

Every knob lives here and is loaded from the environment (or a local ``.env``
file) through pydantic-settings, so nothing is configured by editing code.
"""

from __future__ import annotations

import ipaddress
import platform
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent

WhisperSize = Literal["tiny", "base", "small", "medium", "large-v3"]
ComputeType = Literal["int8", "int8_float16", "float16", "float32"]


class AppCommand(BaseModel):
    """A whitelisted application mapped to a fixed argument vector."""

    model_config = {"frozen": True}

    label: str = Field(min_length=1, max_length=64)
    argv: list[str] = Field(min_length=1, max_length=8)

    @field_validator("argv")
    @classmethod
    def _no_shell_metacharacters(cls, value: list[str]) -> list[str]:
        banned = set("&|;$><\n\r") | {"`"}
        for part in value:
            if banned & set(part):
                raise ValueError(f"shell metacharacters are not allowed in argv: {part!r}")
        return value


def _default_apps() -> dict[str, AppCommand]:
    """Conservative, platform-appropriate application whitelist."""
    system = platform.system()
    if system == "Windows":
        return {
            "notepad": AppCommand(label="Notepad", argv=["notepad.exe"]),
            "calculator": AppCommand(label="Calculator", argv=["calc.exe"]),
            "explorer": AppCommand(label="File Explorer", argv=["explorer.exe"]),
        }
    if system == "Darwin":
        return {
            "notes": AppCommand(label="Notes", argv=["open", "-a", "Notes"]),
            "calculator": AppCommand(label="Calculator", argv=["open", "-a", "Calculator"]),
            "finder": AppCommand(label="Finder", argv=["open", "-a", "Finder"]),
        }
    return {
        "editor": AppCommand(label="Text Editor", argv=["gedit"]),
        "calculator": AppCommand(label="Calculator", argv=["gnome-calculator"]),
        "files": AppCommand(label="Files", argv=["xdg-open", str(Path.home())]),
    }


def _default_roots() -> list[Path]:
    home = Path.home()
    candidates = [home / "Documents", home / "Downloads"]
    found = [p for p in candidates if p.exists()]
    return found or [home]


class Settings(BaseSettings):
    """Runtime configuration for the whole application."""

    model_config = SettingsConfigDict(
        env_prefix="NOVA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = Field(default="Nova", min_length=1, max_length=32)
    environment: Literal["dev", "prod"] = "prod"
    open_browser: bool = True

    # --------------------------------------------------------------- server
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    auth_token: str = Field(default_factory=lambda: secrets.token_urlsafe(32))

    # ------------------------------------------------------------------ llm
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b"
    ollama_timeout_s: float = Field(default=120.0, gt=0, le=600)
    ollama_think: bool = False
    """Thinking is off by default for latency; turn it on for harder questions."""
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    num_ctx: int = Field(default=8192, ge=512, le=131072)
    context_token_budget: int = Field(default=3500, ge=256, le=120000)
    max_tool_iterations: int = Field(default=4, ge=1, le=10)
    tool_timeout_s: float = Field(default=20.0, gt=0, le=120)
    history_turns: int = Field(default=12, ge=1, le=100)

    # ------------------------------------------------------------------ stt
    stt_enabled: bool = True
    whisper_model: WhisperSize = "base"
    whisper_device: Literal["cpu", "cuda", "auto"] = "cpu"
    whisper_compute_type: ComputeType = "int8"
    whisper_language: str | None = "en"
    whisper_beam_size: int = Field(default=1, ge=1, le=10)
    whisper_download_root: Path = PROJECT_ROOT / "models" / "whisper"

    # ------------------------------------------------------------------ tts
    tts_enabled: bool = True
    piper_voice_path: Path = PROJECT_ROOT / "models" / "piper" / "en_US-amy-medium.onnx"
    piper_length_scale: float = Field(default=1.0, ge=0.5, le=2.0)
    piper_noise_scale: float = Field(default=0.667, ge=0.0, le=1.0)
    piper_noise_w: float = Field(default=0.8, ge=0.0, le=1.0)
    tts_chunk_chars: int = Field(default=180, ge=40, le=600)

    # ------------------------------------------------------------------ vad
    vad_enabled: bool = True
    vad_aggressiveness: int = Field(default=2, ge=0, le=3)
    vad_silence_ms: int = Field(default=900, ge=200, le=5000)
    vad_min_speech_ms: int = Field(default=250, ge=100, le=5000)

    # ------------------------------------------------------------- wakeword
    wakeword_enabled: bool = False
    wakeword_model: str = "alexa"
    """Identifier of a bundled openWakeWord model."""
    wakeword_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    # ---------------------------------------------------------------- tools
    weather_enabled: bool = True
    web_search_enabled: bool = True
    web_search_max_results: int = Field(default=5, ge=1, le=10)
    weather_latitude: float | None = Field(default=None, ge=-90, le=90)
    weather_longitude: float | None = Field(default=None, ge=-180, le=180)
    weather_location_name: str = "your saved location"

    allowed_roots: list[Path] = Field(default_factory=_default_roots)
    allowed_apps: dict[str, AppCommand] = Field(default_factory=_default_apps)

    # -------------------------------------------------------------- storage
    db_path: Path = PROJECT_ROOT / "data" / "nova.db"

    # --------------------------------------------------------------- limits
    max_message_chars: int = Field(default=4000, ge=100, le=100_000)
    max_audio_seconds: float = Field(default=60.0, gt=0, le=600)
    max_audio_bytes: int = Field(default=10 * 1024 * 1024, ge=64_000)
    max_file_bytes: int = Field(default=512 * 1024, ge=1024)
    max_ws_message_bytes: int = Field(default=512 * 1024, ge=4096)

    http_rate_limit: int = Field(default=180, ge=1)
    http_rate_window_s: float = Field(default=60.0, gt=0)
    ws_rate_limit: int = Field(default=60, ge=1)
    ws_rate_window_s: float = Field(default=60.0, gt=0)
    audio_rate_limit: int = Field(default=4000, ge=1)
    """Audio frames allowed per window; frames are small and arrive rapidly."""
    audio_rate_window_s: float = Field(default=60.0, gt=0)

    # -------------------------------------------------------------- logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False
    log_file: Path | None = None

    # ----------------------------------------------------------- validators
    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        """Refuse to expose the assistant beyond this machine."""
        if value in {"localhost", "::1"}:
            return value
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError(f"host must be a loopback address, got {value!r}") from exc
        if not address.is_loopback:
            raise ValueError(
                f"refusing to bind to non-loopback address {value!r}; "
                "Nova is a local-only assistant"
            )
        return value

    @field_validator("allowed_roots")
    @classmethod
    def _resolve_roots(cls, value: list[Path]) -> list[Path]:
        resolved: list[Path] = []
        for raw in value:
            path = Path(raw).expanduser().resolve()
            if path not in resolved:
                resolved.append(path)
        if not resolved:
            raise ValueError("at least one allowed filesystem root is required")
        return resolved

    @field_validator("whisper_download_root", "piper_voice_path", "db_path", "log_file")
    @classmethod
    def _expand(cls, value: Path | None) -> Path | None:
        return Path(value).expanduser() if value is not None else None

    @field_validator("auth_token")
    @classmethod
    def _token_strength(cls, value: str) -> str:
        if len(value) < 24:
            raise ValueError("auth_token must be at least 24 characters")
        return value

    @model_validator(mode="after")
    def _coherent(self) -> Settings:
        if self.vad_min_speech_ms > self.max_audio_seconds * 1000:
            raise ValueError("vad_min_speech_ms exceeds max_audio_seconds")
        if (self.weather_latitude is None) != (self.weather_longitude is None):
            raise ValueError("weather_latitude and weather_longitude must be set together")
        return self

    # -------------------------------------------------------------- helpers
    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def allowed_origins(self) -> frozenset[str]:
        """Origins accepted on HTTP and WebSocket requests."""
        hosts = {self.host, "localhost", "127.0.0.1"}
        return frozenset(f"http://{host}:{self.port}" for host in hosts)

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """Host header values accepted (DNS-rebinding defence)."""
        hosts = {self.host, "localhost", "127.0.0.1"}
        with_port = {f"{host}:{self.port}" for host in hosts}
        return frozenset(hosts | with_port)

    @property
    def default_read_root(self) -> Path:
        """Reads under this root are 'safe'; the other roots are 'sensitive'."""
        return self.allowed_roots[0]

    def tokenized_url(self) -> str:
        return f"{self.origin}/?token={self.auth_token}"

    def redacted(self) -> dict[str, object]:
        """Settings snapshot that is safe for logs and the settings UI."""
        data: dict[str, object] = self.model_dump(mode="json")
        data["auth_token"] = "***redacted***"  # noqa: S105  # nosec B105 - a placeholder
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
