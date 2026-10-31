"""Application state and shared FastAPI dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from nova.config import Settings
from nova.core.scheduler import ReminderScheduler
from nova.db.database import Database
from nova.llm.client import OllamaClient
from nova.security.auth import authenticate
from nova.security.ratelimit import RateLimiter, RateLimitExceeded
from nova.security.sandbox import PathSandbox
from nova.tools.base import ToolRegistry
from nova.voice.stt import Transcriber
from nova.voice.tts import Speaker


@dataclass
class AppState:
    """Everything the request handlers need, built once at startup."""

    settings: Settings
    db: Database
    registry: ToolRegistry
    llm: OllamaClient
    sandbox: PathSandbox
    scheduler: ReminderScheduler
    transcriber: Transcriber
    speaker: Speaker
    http_limiter: RateLimiter
    connections: set[Any] = field(default_factory=set)

    async def broadcast(self, event: Any) -> None:
        """Push an event to every live WebSocket session."""
        for session in list(self.connections):
            try:
                await session.send_event(event)
            except Exception:  # a dead socket must not block the others
                self.connections.discard(session)


def get_state(request: Request) -> AppState:
    """Fetch the application state from the FastAPI app."""
    state: AppState | None = getattr(request.app.state, "nova", None)
    if state is None:  # pragma: no cover - only if startup failed
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Nova is still starting")
    return state


def get_settings_dep(state: Annotated[AppState, Depends(get_state)]) -> Settings:
    return state.settings


def client_key(request: Request) -> str:
    """Rate-limit key: the client address, with a fallback for odd transports."""
    client = request.client
    return client.host if client else "unknown"


def rate_limit(
    request: Request,
    state: Annotated[AppState, Depends(get_state)],
) -> None:
    """Apply the per-IP HTTP rate limit."""
    try:
        state.http_limiter.check(client_key(request))
    except RateLimitExceeded as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests",
            headers={"Retry-After": str(int(exc.retry_after) + 1)},
        ) from exc


def require_auth(
    request: Request,
    state: Annotated[AppState, Depends(get_state)],
    _limit: Annotated[None, Depends(rate_limit)],
) -> AppState:
    """Authenticate the request and hand back the application state."""
    authenticate(request, state.settings)
    return state


AuthorisedState = Annotated[AppState, Depends(require_auth)]
StateDep = Annotated[AppState, Depends(get_state)]
