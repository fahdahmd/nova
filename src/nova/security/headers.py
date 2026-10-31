"""Security response headers, including a strict self-only CSP."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


def content_security_policy() -> str:
    """A CSP with no inline scripts, no eval and no third-party origins.

    Every asset is self-hosted, so ``'self'`` is enough everywhere. ``connect-src``
    additionally allows the loopback WebSocket scheme.
    """
    directives = [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "media-src 'self' blob:",
        "connect-src 'self' ws://127.0.0.1:* ws://localhost:*",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "require-trusted-types-for 'script'",
    ]
    return "; ".join(directives)


SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": content_security_policy(),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Microphone is the only capability Nova needs, and only for its own origin.
    "Permissions-Policy": (
        "microphone=(self), camera=(), geolocation=(), payment=(), usb=(), "
        "display-capture=(), midi=(), serial=()"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cache-Control": "no-store",
    "X-Permitted-Cross-Domain-Policies": "none",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the security headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
