"""HTTP routes.

Everything except ``/api/health`` requires the session token. The index accepts
the token from the tokenized URL and immediately stores it in an HttpOnly,
SameSite=Strict cookie, so no later request needs it in the URL.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from nova.api.deps import AuthorisedState, StateDep, rate_limit
from nova.config import PACKAGE_ROOT
from nova.logging_setup import get_logger
from nova.security.auth import SESSION_COOKIE, extract_token, token_matches

log = get_logger(__name__)

router = APIRouter()
STATIC_DIR = (PACKAGE_ROOT / "static").resolve()

# Only these media types are ever served from the static directory.
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
}

UNAUTHORISED_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Nova - token required</title>
<style>body{font:16px/1.6 system-ui,sans-serif;background:#0b0d12;color:#e6e9f0;
margin:0;display:grid;place-items:center;height:100vh}main{max-width:34rem;padding:2rem}
code{background:#1a1f2b;padding:.15rem .4rem;border-radius:.3rem}</style></head>
<body><main><h1>Session token required</h1>
<p>Nova prints a one-click link when it starts. Open that link, which looks like
<code>http://127.0.0.1:8765/?token=...</code></p>
<p>If you have lost it, restart Nova to print a fresh one.</p></main></body></html>"""


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------
class PreferencePayload(BaseModel):
    model_config = {"extra": "forbid"}

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_]+$")
    value: str = Field(max_length=256)


class NotePayload(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=8000)


class TodoPayload(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1, max_length=300)


# ---------------------------------------------------------------------------
# page and assets
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(
    request: Request,
    state: StateDep,
    token: Annotated[str | None, Query(max_length=128)] = None,
    _limit: Annotated[None, Depends(rate_limit)] = None,
) -> Response:
    """Serve the app shell and establish the session cookie."""
    settings = state.settings
    # Accept the token from the URL, the cookie or the header, like every
    # other route, so a reload and an API call behave the same way.
    candidate = token or extract_token(request)
    if not token_matches(candidate, settings.auth_token):
        return HTMLResponse(UNAUTHORISED_PAGE, status_code=status.HTTP_401_UNAUTHORIZED)

    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():  # pragma: no cover - packaging guard
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "UI assets are missing")

    html = index_file.read_text(encoding="utf-8").replace("{{APP_NAME}}", settings.app_name)
    response = HTMLResponse(html)
    response.set_cookie(
        SESSION_COOKIE,
        settings.auth_token,
        httponly=True,
        samesite="strict",
        secure=False,  # loopback HTTP; the cookie never leaves this machine
        path="/",
        max_age=12 * 3600,
    )
    return response


@router.get("/static/{asset_path:path}", include_in_schema=False)
async def static_asset(
    asset_path: str,
    request: Request,
    state: StateDep,
    _limit: Annotated[None, Depends(rate_limit)] = None,
) -> Response:
    """Serve a bundled asset, authenticated and strictly path-checked."""
    if not token_matches(extract_token(request), state.settings.auth_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session token required")

    candidate = (STATIC_DIR / asset_path).resolve()
    if candidate.parent != STATIC_DIR and STATIC_DIR not in candidate.parents:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not candidate.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    media_type = _CONTENT_TYPES.get(candidate.suffix.lower())
    if media_type is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    return FileResponse(candidate, media_type=media_type)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    icon = STATIC_DIR / "favicon.svg"
    if icon.is_file():
        return FileResponse(icon, media_type="image/svg+xml")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
@router.get("/api/health", include_in_schema=False)
async def health(state: StateDep) -> JSONResponse:
    """Liveness probe. Deliberately unauthenticated and free of detail."""
    return JSONResponse(
        {
            "status": "ok",
            "app": state.settings.app_name,
            "scheduler": state.scheduler.running,
        }
    )


# ---------------------------------------------------------------------------
# state for the side panels
# ---------------------------------------------------------------------------
@router.get("/api/state")
async def app_state(state: AuthorisedState) -> dict[str, Any]:
    """Everything the side panels and settings drawer display."""
    db = state.db
    settings = state.settings

    notes, todos, reminders, memories, audit, preferences = await asyncio.gather(
        asyncio.to_thread(db.list_notes, 50),
        asyncio.to_thread(db.list_todos, include_done=True, limit=50),
        asyncio.to_thread(db.list_reminders, pending_only=False, limit=50),
        asyncio.to_thread(db.list_memories, 100),
        asyncio.to_thread(db.list_audit, 100),
        asyncio.to_thread(db.get_preferences),
    )

    return {
        "notes": [item.model_dump(mode="json") for item in notes],
        "todos": [item.model_dump(mode="json") for item in todos],
        "reminders": [item.model_dump(mode="json") for item in reminders],
        "memory": [item.model_dump(mode="json") for item in memories],
        "audit": [item.model_dump(mode="json") for item in audit],
        "preferences": preferences,
        "config": {
            "app_name": settings.app_name,
            "model": settings.ollama_model,
            "thinking": settings.ollama_think,
            "voice": settings.piper_voice_path.stem,
            "voice_available": state.speaker.available,
            "stt_available": state.transcriber.available,
            "weather_enabled": settings.weather_enabled,
            "web_search_enabled": settings.web_search_enabled,
            "wakeword_enabled": settings.wakeword_enabled,
            "allowed_roots": state.sandbox.describe(),
            "allowed_apps": [
                {"app": key, "label": command.label}
                for key, command in settings.allowed_apps.items()
            ],
            "tools": state.registry.names(settings),
        },
    }


@router.get("/api/messages")
async def messages(state: AuthorisedState) -> dict[str, Any]:
    """Recent conversation history, for restoring the transcript on reload."""
    history = await asyncio.to_thread(state.db.recent_messages, 40)
    return {"messages": [item.model_dump(mode="json") for item in history]}


# ---------------------------------------------------------------------------
# mutations driven by the UI (not by the model)
# ---------------------------------------------------------------------------
@router.post("/api/preferences", status_code=status.HTTP_204_NO_CONTENT)
async def set_preference(payload: PreferencePayload, state: AuthorisedState) -> Response:
    await asyncio.to_thread(state.db.set_preference, payload.key, payload.value)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/todos", status_code=status.HTTP_201_CREATED)
async def create_todo(payload: TodoPayload, state: AuthorisedState) -> dict[str, Any]:
    todo = await asyncio.to_thread(state.db.add_todo, payload.text)
    return todo.model_dump(mode="json")


@router.post("/api/todos/{todo_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_todo(todo_id: int, state: AuthorisedState) -> Response:
    if not await asyncio.to_thread(state.db.complete_todo, todo_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such open todo")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(todo_id: int, state: AuthorisedState) -> Response:
    if not await asyncio.to_thread(state.db.delete_todo, todo_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such todo")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/notes", status_code=status.HTTP_201_CREATED)
async def create_note(payload: NotePayload, state: AuthorisedState) -> dict[str, Any]:
    note = await asyncio.to_thread(state.db.create_note, payload.title, payload.body)
    return note.model_dump(mode="json")


@router.delete("/api/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: int, state: AuthorisedState) -> Response:
    if not await asyncio.to_thread(state.db.delete_note, note_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such note")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_reminder(reminder_id: int, state: AuthorisedState) -> Response:
    if not await asyncio.to_thread(state.scheduler.cancel, reminder_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such pending reminder")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_memory(memory_id: int, state: AuthorisedState) -> Response:
    if not await asyncio.to_thread(state.db.forget_memory, memory_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such memory")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/memory", status_code=status.HTTP_200_OK)
async def clear_memory(state: AuthorisedState) -> dict[str, int]:
    removed = await asyncio.to_thread(state.db.clear_memories)
    return {"removed": removed}


@router.delete("/api/messages", status_code=status.HTTP_200_OK)
async def clear_messages(state: AuthorisedState) -> dict[str, int]:
    removed = await asyncio.to_thread(state.db.clear_messages)
    return {"removed": removed}


@router.delete("/api/audit", status_code=status.HTTP_200_OK)
async def clear_audit(state: AuthorisedState) -> dict[str, int]:
    removed = await asyncio.to_thread(state.db.clear_audit)
    return {"removed": removed}


def static_file(name: str) -> Path:
    """Resolve a bundled static file (used by tests)."""
    return STATIC_DIR / name
