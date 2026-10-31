"""The WebSocket endpoint.

One socket carries everything: text chat, microphone audio in, synthesised
speech out, tool progress, confirmation prompts and reminder toasts.

Security at the handshake: Host and Origin are checked before the upgrade is
accepted, then the session token. Per-connection rate limits and size caps apply
to every frame afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from nova.api.deps import AppState
from nova.core.events import (
    CLIENT_EVENT_ADAPTER,
    AudioEnd,
    AudioHeader,
    AudioStartIn,
    CancelIn,
    ChatIn,
    ConfirmIn,
    ConfirmRequest,
    ErrorOut,
    PingIn,
    Pong,
    PreferenceIn,
    Ready,
    ReminderFired,
    ServerEvent,
    Status,
    Transcript,
)
from nova.db.models import Reminder
from nova.llm.orchestrator import Orchestrator
from nova.logging_setup import get_logger
from nova.security.auth import check_host, check_origin, token_matches
from nova.security.ratelimit import RateLimiter, RateLimitExceeded
from nova.voice.stt import SttUnavailable
from nova.voice.tts import TtsUnavailable
from nova.voice.vad import SpeechGate
from nova.voice.wakeword import WakeWordDetector

log = get_logger(__name__)

router = APIRouter()

CONFIRMATION_TIMEOUT_S = 180.0
BYTES_PER_SECOND_16K = 32000


class Session:
    """One connected browser tab."""

    def __init__(self, websocket: WebSocket, state: AppState) -> None:
        self._ws = websocket
        self._state = state
        self._settings = state.settings
        self._send_lock = asyncio.Lock()
        self._closed = False

        self._message_limiter = RateLimiter(
            limit=self._settings.ws_rate_limit, window_s=self._settings.ws_rate_window_s
        )
        self._audio_limiter = RateLimiter(
            limit=self._settings.audio_rate_limit, window_s=self._settings.audio_rate_window_s
        )

        self._orchestrator = Orchestrator(
            settings=state.settings,
            db=state.db,
            registry=state.registry,
            llm=state.llm,
            sandbox=state.sandbox,
            scheduler=state.scheduler,
        )

        self._turn_task: asyncio.Task[None] | None = None
        self._cancelled = asyncio.Event()
        self._pending_confirmations: dict[str, asyncio.Future[bool]] = {}

        self._recording = False
        self._audio = bytearray()
        self._gate: SpeechGate | None = None
        self._wakeword = WakeWordDetector(state.settings)
        self._hands_free = False
        self._muted = False
        self._voice_enabled = True
        self._think: bool | None = None

    # ------------------------------------------------------------------ send
    async def send_event(self, event: ServerEvent) -> None:
        """Send one JSON event, serialised so concurrent tasks cannot interleave."""
        if self._closed:
            return
        async with self._send_lock:
            try:
                await self._ws.send_json(event.payload())
            except (WebSocketDisconnect, RuntimeError):
                self._closed = True

    async def _send_audio(self, pcm: bytes) -> None:
        if self._closed:
            return
        async with self._send_lock:
            try:
                await self._ws.send_bytes(pcm)
            except (WebSocketDisconnect, RuntimeError):
                self._closed = True

    # ------------------------------------------------------------- lifecycle
    async def run(self) -> None:
        """Receive loop. Returns when the socket closes."""
        await self._send_ready()
        try:
            while True:
                frame = await self._ws.receive()
                kind = frame.get("type")
                if kind == "websocket.disconnect":
                    break
                if "text" in frame and frame["text"] is not None:
                    await self._on_text(frame["text"])
                elif "bytes" in frame and frame["bytes"] is not None:
                    await self._on_audio(frame["bytes"])
        except WebSocketDisconnect:
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        self._closed = True
        self._cancelled.set()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn_task
        for future in self._pending_confirmations.values():
            if not future.done():
                future.set_result(False)
        self._pending_confirmations.clear()

    async def _send_ready(self) -> None:
        state = self._state
        settings = self._settings
        preferences = await asyncio.to_thread(state.db.get_preferences)
        self._muted = preferences.get("muted") == "true"
        self._voice_enabled = preferences.get("voice_enabled") != "false"
        self._hands_free = preferences.get("hands_free") == "true"
        if "thinking" in preferences:
            self._think = preferences["thinking"] == "true"
        await self.send_event(
            Ready(
                app_name=settings.app_name,
                model=settings.ollama_model,
                tools=state.registry.names(settings),
                voice_available=state.speaker.available,
                stt_available=state.transcriber.available,
                hands_free_available=settings.vad_enabled,
                wakeword_available=self._wakeword.available,
                thinking=settings.ollama_think,
                allowed_roots=state.sandbox.describe(),
                allowed_apps=[
                    {"app": key, "label": command.label}
                    for key, command in settings.allowed_apps.items()
                ],
                preferences=preferences,
            )
        )

    # --------------------------------------------------------- text messages
    async def _on_text(self, raw: str) -> None:
        if len(raw.encode("utf-8")) > self._settings.max_ws_message_bytes:
            await self.send_event(ErrorOut(message="That message is too large.", code="too_large"))
            return
        try:
            self._message_limiter.check("messages")
        except RateLimitExceeded as exc:
            await self.send_event(
                ErrorOut(
                    message="Slow down a moment.",
                    code="rate_limited",
                    hint=f"Try again in {exc.retry_after:.0f}s.",
                )
            )
            return

        try:
            event = CLIENT_EVENT_ADAPTER.validate_json(raw)
        except ValidationError:
            await self.send_event(
                ErrorOut(message="That message was not understood.", code="bad_request")
            )
            return

        if isinstance(event, PingIn):
            await self.send_event(Pong())
        elif isinstance(event, ChatIn):
            await self._start_turn(event.text)
        elif isinstance(event, CancelIn):
            await self._cancel_turn()
        elif isinstance(event, ConfirmIn):
            self._resolve_confirmation(event)
        elif isinstance(event, AudioStartIn):
            await self._audio_start(event)
        elif isinstance(event, PreferenceIn):
            await self._set_preference(event)
        else:  # AudioEndIn
            await self._audio_end(cancelled=getattr(event, "cancelled", False))

    async def _set_preference(self, event: PreferenceIn) -> None:
        await asyncio.to_thread(self._state.db.set_preference, event.key, event.value)
        if event.key == "muted":
            self._muted = event.value == "true"
        elif event.key == "voice_enabled":
            self._voice_enabled = event.value == "true"
        elif event.key == "hands_free":
            self._hands_free = event.value == "true"
        elif event.key == "thinking":
            self._think = event.value == "true"

    # ------------------------------------------------------------ audio in
    async def _audio_start(self, event: AudioStartIn) -> None:
        if not self._state.transcriber.available:
            await self.send_event(
                ErrorOut(
                    message="Speech to text is turned off.",
                    code="stt_disabled",
                    hint="Set NOVA_STT_ENABLED=true to use the microphone.",
                )
            )
            return

        # Speaking is barge-in: stop whatever is playing or generating.
        await self._cancel_turn(quiet=True)

        self._recording = True
        self._audio = bytearray()
        self._hands_free = event.mode == "hands_free"
        self._gate = (
            SpeechGate(
                aggressiveness=self._settings.vad_aggressiveness,
                silence_ms=self._settings.vad_silence_ms,
                min_speech_ms=self._settings.vad_min_speech_ms,
            )
            if self._hands_free and self._settings.vad_enabled
            else None
        )
        self._wakeword.reset()
        await self.send_event(Status(state="listening"))

    async def _on_audio(self, chunk: bytes) -> None:
        if not self._recording:
            return
        try:
            self._audio_limiter.check("audio")
        except RateLimitExceeded:
            await self._audio_end(cancelled=True)
            await self.send_event(
                ErrorOut(message="Audio is arriving too fast.", code="rate_limited")
            )
            return

        if len(chunk) > self._settings.max_ws_message_bytes:
            await self._audio_end(cancelled=True)
            await self.send_event(
                ErrorOut(message="That audio frame is too large.", code="too_large")
            )
            return

        self._audio.extend(chunk)

        if len(self._audio) > self._settings.max_audio_bytes:
            await self.send_event(
                ErrorOut(
                    message="That recording is too long.",
                    code="too_long",
                    hint=f"The limit is {self._settings.max_audio_seconds:.0f} seconds.",
                )
            )
            await self._audio_end(cancelled=True)
            return

        if self._gate is not None:
            result = self._gate.feed(bytes(chunk))
            if result.speech_ended:
                await self._audio_end(cancelled=False)

    async def _audio_end(self, *, cancelled: bool) -> None:
        if not self._recording:
            return
        self._recording = False
        pcm = bytes(self._audio)
        self._audio = bytearray()
        self._gate = None

        if cancelled or not pcm:
            await self.send_event(Status(state="idle"))
            return

        seconds = len(pcm) / BYTES_PER_SECOND_16K
        if seconds < 0.25:
            await self.send_event(ErrorOut(message="That was too short to hear.", code="too_short"))
            await self.send_event(Status(state="idle"))
            return

        await self.send_event(Status(state="thinking", detail="transcribing"))
        try:
            text = await self._state.transcriber.transcribe(pcm)
        except SttUnavailable as exc:
            await self.send_event(ErrorOut(message=str(exc), code="stt_failed"))
            await self.send_event(Status(state="idle"))
            return

        if not text.strip():
            await self.send_event(
                ErrorOut(message="I did not catch that.", code="empty_transcript")
            )
            await self.send_event(Status(state="idle"))
            return

        await self.send_event(Transcript(text=text))
        await self._start_turn(text, speak=True)

    # ------------------------------------------------------------------ turn
    async def _start_turn(self, text: str, *, speak: bool = False) -> None:
        if len(text) > self._settings.max_message_chars:
            await self.send_event(ErrorOut(message="That message is too long.", code="too_long"))
            return
        if self._turn_task is not None and not self._turn_task.done():
            await self.send_event(
                ErrorOut(message="Still working on the last message.", code="busy")
            )
            return

        self._cancelled = asyncio.Event()
        self._turn_task = asyncio.create_task(self._run_turn(text, speak=speak))

    async def _run_turn(self, text: str, *, speak: bool) -> None:
        try:
            result = await self._orchestrator.run_turn(
                text,
                emit=self.send_event,
                confirm=self._confirm,
                cancelled=self._cancelled,
                think=self._think,
            )

            # "Speak replies aloud" governs every reply, typed or spoken.
            should_speak = bool(
                result.text
                and not result.cancelled
                and self._voice_enabled
                and not self._muted
                and self._state.speaker.available
            )
            if should_speak:
                await self._speak(result.text)
            await self.send_event(Status(state="idle"))
        except asyncio.CancelledError:
            await self.send_event(Status(state="idle"))
            raise
        except Exception:
            log.exception("turn.failed")
            await self.send_event(
                ErrorOut(message="Something went wrong handling that.", code="internal")
            )
            await self.send_event(Status(state="idle"))

    async def _cancel_turn(self, *, quiet: bool = False) -> None:
        """Barge-in: stop generating and stop speaking immediately."""
        self._cancelled.set()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn_task
        if not quiet:
            await self.send_event(AudioEnd(interrupted=True))
            await self.send_event(Status(state="idle"))

    # ----------------------------------------------------------------- speech
    async def _speak(self, text: str) -> None:
        speaker = self._state.speaker
        try:
            first = True
            async for frame in speaker.stream(text, cancelled=self._cancelled):
                if first:
                    await self.send_event(
                        AudioHeader(sample_rate=frame.sample_rate, utterance=text[:200])
                    )
                    await self.send_event(Status(state="speaking"))
                    first = False
                await self._send_audio(frame.pcm)
            await self.send_event(AudioEnd(interrupted=self._cancelled.is_set()))
        except TtsUnavailable as exc:
            await self.send_event(
                ErrorOut(
                    message=str(exc),
                    code="tts_unavailable",
                    hint="Run `python scripts/download_voice.py` to install a voice.",
                )
            )

    # ----------------------------------------------------------- confirmation
    async def _confirm(self, request: ConfirmRequest) -> bool:
        """Ask the UI and wait. A timeout or a disconnect counts as 'no'."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending_confirmations[request.request_id] = future
        await self.send_event(request)
        try:
            return await asyncio.wait_for(future, timeout=CONFIRMATION_TIMEOUT_S)
        except TimeoutError:
            log.info("confirm.timeout", tool=request.tool)
            return False
        except asyncio.CancelledError:
            return False
        finally:
            self._pending_confirmations.pop(request.request_id, None)

    def _resolve_confirmation(self, event: ConfirmIn) -> None:
        future = self._pending_confirmations.get(event.request_id)
        if future is not None and not future.done():
            future.set_result(event.approved)

    # -------------------------------------------------------------- reminders
    async def push_reminder(self, reminder: Reminder) -> None:
        await self.send_event(
            ReminderFired(id=reminder.id, text=reminder.text, due_at=reminder.due_at)
        )


def _authorise(websocket: WebSocket, state: AppState) -> bool:
    """Validate Host, Origin and token before accepting the upgrade."""
    settings = state.settings
    if not check_host(websocket.headers.get("host"), settings):
        return False
    # A WebSocket upgrade is not subject to CORS, so Origin is mandatory here.
    if not check_origin(websocket.headers.get("origin"), settings, required=True):
        return False

    token = (
        websocket.headers.get("x-nova-token")
        or websocket.cookies.get("nova_session")
        or websocket.query_params.get("token")
    )
    return token_matches(token, settings.auth_token)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Accept an authorised WebSocket and run a session on it."""
    state: AppState | None = getattr(websocket.app.state, "nova", None)
    if state is None:  # pragma: no cover - startup guard
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    if not _authorise(websocket, state):
        log.warning(
            "ws.rejected",
            origin=websocket.headers.get("origin"),
            host=websocket.headers.get("host"),
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    session = Session(websocket, state)
    state.connections.add(session)
    log.info("ws.connected", connections=len(state.connections))
    try:
        await session.run()
    finally:
        state.connections.discard(session)
        await session.close()
        log.info("ws.disconnected", connections=len(state.connections))


async def broadcast_reminder(state: AppState, reminder: Reminder) -> None:
    """Scheduler callback: push a fired reminder to every open tab."""
    for session in list(state.connections):
        try:
            await session.push_reminder(reminder)
        except Exception:  # one bad socket must not stop the rest
            state.connections.discard(session)
