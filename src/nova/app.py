"""FastAPI application factory.

Wires the middleware stack, builds the application state during the lifespan,
and installs exception handlers that never leak a traceback to the browser.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from nova import __version__
from nova.api import routes, ws
from nova.api.deps import AppState
from nova.config import Settings, get_settings
from nova.core.scheduler import ReminderScheduler
from nova.db.database import Database, get_database
from nova.db.models import Reminder
from nova.llm.client import OllamaClient
from nova.logging_setup import configure_logging, get_logger
from nova.security.headers import SecurityHeadersMiddleware
from nova.security.ratelimit import RateLimiter
from nova.security.sandbox import build_sandbox
from nova.tools import build_registry
from nova.voice.stt import Transcriber
from nova.voice.tts import Speaker

log = get_logger(__name__)


def build_state(settings: Settings, *, db: Database | None = None) -> AppState:
    """Assemble the application state (shared by the app and the tests)."""
    database = db or get_database(settings.db_path)
    scheduler = ReminderScheduler(database, app_name=settings.app_name)
    return AppState(
        settings=settings,
        db=database,
        registry=build_registry(),
        llm=OllamaClient(settings),
        sandbox=build_sandbox(settings.allowed_roots, settings.max_file_bytes),
        scheduler=scheduler,
        transcriber=Transcriber(settings),
        speaker=Speaker(settings),
        http_limiter=RateLimiter(
            limit=settings.http_rate_limit, window_s=settings.http_rate_window_s
        ),
    )


def create_app(settings: Settings | None = None, *, db: Database | None = None) -> FastAPI:
    """Create the configured application."""
    config = settings or get_settings()
    configure_logging(
        config.log_level,
        json_logs=config.log_json,
        log_file=str(config.log_file) if config.log_file else None,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = build_state(config, db=db)
        app.state.nova = state

        async def on_reminder(reminder: Reminder) -> None:
            await ws.broadcast_reminder(state, reminder)

        state.scheduler.set_callback(on_reminder)
        state.scheduler.start()

        # Warm the heavy pieces in the background so the UI is usable at once.
        import asyncio

        warmups = [
            asyncio.create_task(state.llm.warmup()),
            asyncio.create_task(state.transcriber.preload()),
            asyncio.create_task(state.speaker.preload()),
        ]

        log.info(
            "app.started",
            version=__version__,
            model=config.ollama_model,
            tools=len(state.registry),
            voice=state.speaker.available,
        )
        try:
            yield
        finally:
            for task in warmups:
                task.cancel()
            state.scheduler.shutdown()
            for session in list(state.connections):
                with contextlib.suppress(Exception):
                    await session.close()
            state.db.close()
            log.info("app.stopped")

    app = FastAPI(
        title=f"{config.app_name} local assistant",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Host allow-list first: reject rebinding attempts before anything else runs.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(config.allowed_hosts))
    app.add_middleware(SecurityHeadersMiddleware)
    # Note: no CORS middleware at all. Cross-origin requests have no business
    # here, and a wildcard policy would defeat the token entirely.

    app.include_router(routes.router)
    app.include_router(ws.router)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        log.info("request.invalid", path=request.url.path)
        return JSONResponse(
            status_code=422,
            content={"detail": "The request could not be validated."},
        )

    @app.exception_handler(HTTPException)
    async def on_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the detail, tell the client nothing.
        log.exception("request.failed", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong. Check the Nova console for details."},
        )

    return app


def get_app() -> Any:
    """Entry point for ``uvicorn nova.app:get_app --factory``."""
    return create_app()
