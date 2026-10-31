"""Session-token authentication plus Origin/Host validation.

Nova listens on loopback only, but a loopback port is still reachable from any
page the browser happens to load. Two defences are therefore mandatory on every
request:

* a random per-process session token, compared in constant time;
* strict ``Origin``/``Host`` checks, which stop DNS rebinding and cross-site
  WebSocket hijacking (a WebSocket handshake is not subject to CORS).
"""

from __future__ import annotations

import hmac
from typing import Final
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from nova.config import Settings

TOKEN_HEADER: Final = "x-nova-token"
SESSION_COOKIE: Final = "nova_session"
QUERY_PARAM: Final = "token"


def token_matches(candidate: str | None, expected: str) -> bool:
    """Constant-time token comparison that never raises on odd input."""
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _normalise_host(value: str) -> str:
    """Strip any userinfo and lowercase a Host header value."""
    host = value.strip().lower()
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    return host


def check_host(host_header: str | None, settings: Settings) -> bool:
    """Validate the Host header against the configured loopback names."""
    if not host_header:
        return False
    return _normalise_host(host_header) in settings.allowed_hosts


def check_origin(origin: str | None, settings: Settings, *, required: bool) -> bool:
    """Validate an Origin header.

    ``required=True`` is used for WebSocket upgrades and state-changing HTTP
    requests, where a missing Origin is treated as a failure. Plain navigations
    (``GET /``) legitimately arrive without one.
    """
    if origin is None:
        return not required
    if origin == "null":
        return False
    parts = urlsplit(origin)
    if parts.scheme != "http" or not parts.netloc:
        return False
    return f"{parts.scheme}://{parts.netloc.lower()}" in settings.allowed_origins


def extract_token(request: Request) -> str | None:
    """Pull the session token from header, cookie, or query string."""
    header = request.headers.get(TOKEN_HEADER)
    if header:
        return header
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie
    return request.query_params.get(QUERY_PARAM)


def authenticate(request: Request, settings: Settings, *, origin_required: bool = True) -> None:
    """Authenticate an HTTP request or raise an ``HTTPException``.

    Order matters: host and origin are checked before the token so that a
    cross-origin attacker learns nothing about token validity.
    """
    if not check_host(request.headers.get("host"), settings):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Host header")

    unsafe_method = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    if not check_origin(
        request.headers.get("origin"), settings, required=origin_required and unsafe_method
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid Origin")

    if not token_matches(extract_token(request), settings.auth_token):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing or invalid session token",
            headers={"WWW-Authenticate": "Token"},
        )
