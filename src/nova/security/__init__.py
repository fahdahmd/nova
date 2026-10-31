"""Security primitives: authentication, headers, rate limiting, sandboxing."""

from __future__ import annotations

from nova.security.auth import (
    SESSION_COOKIE,
    TOKEN_HEADER,
    authenticate,
    check_host,
    check_origin,
    token_matches,
)
from nova.security.headers import SecurityHeadersMiddleware, content_security_policy
from nova.security.ratelimit import RateLimiter, RateLimitExceeded
from nova.security.sandbox import PathSandbox, SandboxError

__all__ = [
    "SESSION_COOKIE",
    "TOKEN_HEADER",
    "PathSandbox",
    "RateLimitExceeded",
    "RateLimiter",
    "SandboxError",
    "SecurityHeadersMiddleware",
    "authenticate",
    "check_host",
    "check_origin",
    "content_security_policy",
    "token_matches",
]
