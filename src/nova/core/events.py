"""WebSocket message schemas.

Incoming messages are validated against :data:`ClientEvent` before anything
acts on them, so a malformed or hostile frame is rejected at the edge. Outgoing
messages are built from the typed models below.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

AssistantState = Literal["idle", "listening", "thinking", "speaking"]


# ---------------------------------------------------------------------------
# client -> server
# ---------------------------------------------------------------------------
class ChatIn(BaseModel):
    model_config = {"extra": "forbid"}

    type: Literal["chat"]
    text: str = Field(min_length=1, max_length=8000)


class AudioStartIn(BaseModel):
    model_config = {"extra": "forbid"}

    type: Literal["audio_start"]
    mode: Literal["push_to_talk", "hands_free"] = "push_to_talk"
    sample_rate: int = Field(default=16000, ge=8000, le=48000)


class AudioEndIn(BaseModel):
    model_config = {"extra": "forbid"}

    type: Literal["audio_end"]
    cancelled: bool = False


class CancelIn(BaseModel):
    """Barge-in: stop generating and stop speaking."""

    model_config = {"extra": "forbid"}

    type: Literal["cancel"]


class ConfirmIn(BaseModel):
    model_config = {"extra": "forbid"}

    type: Literal["confirm"]
    request_id: str = Field(min_length=8, max_length=64)
    approved: bool


class PreferenceIn(BaseModel):
    model_config = {"extra": "forbid"}

    type: Literal["preference"]
    key: Literal["theme", "voice_enabled", "speech_rate", "muted", "hands_free", "thinking"]
    value: str = Field(max_length=64)


class PingIn(BaseModel):
    model_config = {"extra": "forbid"}

    type: Literal["ping"]


ClientEvent = Annotated[
    ChatIn | AudioStartIn | AudioEndIn | CancelIn | ConfirmIn | PreferenceIn | PingIn,
    Field(discriminator="type"),
]

CLIENT_EVENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(ClientEvent)


# ---------------------------------------------------------------------------
# server -> client
# ---------------------------------------------------------------------------
class ServerEvent(BaseModel):
    """Base class for outbound events."""

    type: str

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Ready(ServerEvent):
    type: Literal["ready"] = "ready"
    app_name: str
    model: str
    tools: list[str]
    voice_available: bool
    stt_available: bool
    hands_free_available: bool
    wakeword_available: bool
    thinking: bool
    allowed_roots: list[str]
    allowed_apps: list[dict[str, str]]
    preferences: dict[str, str] = Field(default_factory=dict)


class Status(ServerEvent):
    type: Literal["status"] = "status"
    state: AssistantState
    detail: str = ""


class Token(ServerEvent):
    type: Literal["token"] = "token"
    text: str


class StreamReset(ServerEvent):
    """Discard the in-progress assistant bubble: that text was a tool preamble."""

    type: Literal["stream_reset"] = "stream_reset"


class MessageOut(ServerEvent):
    type: Literal["message"] = "message"
    role: Literal["user", "assistant"]
    content: str
    final: bool = True


class Transcript(ServerEvent):
    type: Literal["transcript"] = "transcript"
    text: str
    final: bool = True


class ToolStart(ServerEvent):
    type: Literal["tool_start"] = "tool_start"
    tool: str
    risk: str
    summary: str = ""


class ToolEnd(ServerEvent):
    type: Literal["tool_end"] = "tool_end"
    tool: str
    ok: bool
    detail: str = ""
    duration_ms: int = 0


class ConfirmRequest(ServerEvent):
    type: Literal["confirm_request"] = "confirm_request"
    request_id: str
    tool: str
    risk: str
    prompt: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    from_untrusted_content: bool = False


class Refresh(ServerEvent):
    type: Literal["refresh"] = "refresh"
    collections: list[str]


class ReminderFired(ServerEvent):
    type: Literal["reminder"] = "reminder"
    id: int
    text: str
    due_at: datetime


class AudioHeader(ServerEvent):
    """Announces that binary audio frames follow."""

    type: Literal["audio_header"] = "audio_header"
    sample_rate: int
    channels: int = 1
    sample_width: int = 2
    utterance: str = ""


class AudioEnd(ServerEvent):
    type: Literal["audio_end"] = "audio_end"
    interrupted: bool = False


class ErrorOut(ServerEvent):
    type: Literal["error"] = "error"
    message: str
    code: str = "error"
    hint: str = ""


class Pong(ServerEvent):
    type: Literal["pong"] = "pong"
