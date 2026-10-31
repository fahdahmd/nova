"""Rate limiting, message size caps and audio limits."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from nova.config import Settings
from nova.db.database import Database
from nova.security.ratelimit import RateLimiter, RateLimitExceeded

from .conftest import ORIGIN, WS_HEADERS


# ----------------------------------------------------------------- unit level
def test_allows_exactly_the_configured_number_of_events() -> None:
    limiter = RateLimiter(limit=3, window_s=60)
    for _ in range(3):
        limiter.check("ip")
    with pytest.raises(RateLimitExceeded):
        limiter.check("ip")


def test_keys_are_independent() -> None:
    limiter = RateLimiter(limit=1, window_s=60)
    limiter.check("a")
    limiter.check("b")
    with pytest.raises(RateLimitExceeded):
        limiter.check("a")


def test_the_window_slides() -> None:
    limiter = RateLimiter(limit=2, window_s=0.25)
    limiter.check("ip")
    limiter.check("ip")
    with pytest.raises(RateLimitExceeded):
        limiter.check("ip")
    time.sleep(0.3)
    limiter.check("ip")  # the window has moved on


def test_retry_after_is_reported() -> None:
    limiter = RateLimiter(limit=1, window_s=30)
    limiter.check("ip")
    with pytest.raises(RateLimitExceeded) as info:
        limiter.check("ip")
    assert 0 < info.value.retry_after <= 30
    assert info.value.key == "ip"


def test_remaining_counts_down_and_reset_clears() -> None:
    limiter = RateLimiter(limit=3, window_s=60)
    assert limiter.remaining("ip") == 3
    limiter.check("ip")
    assert limiter.remaining("ip") == 2
    limiter.reset("ip")
    assert limiter.remaining("ip") == 3


def test_allow_is_the_non_raising_variant() -> None:
    limiter = RateLimiter(limit=1, window_s=60)
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is False


def test_reset_without_a_key_clears_everything() -> None:
    limiter = RateLimiter(limit=1, window_s=60)
    limiter.check("a")
    limiter.check("b")
    limiter.reset()
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (-1, 60), (1, 0), (1, -5)])
def test_nonsense_configuration_is_refused(limit: int, window: float) -> None:
    with pytest.raises(ValueError):
        RateLimiter(limit=limit, window_s=window)


# ------------------------------------------------------------------- http
def test_http_requests_are_rate_limited(settings: Settings, db: Database) -> None:
    from nova.app import create_app

    throttled = settings.model_copy(update={"http_rate_limit": 5, "http_rate_window_s": 60.0})
    app = create_app(throttled, db=db)

    with TestClient(app, base_url=ORIGIN) as client:
        client.headers.update({"X-Nova-Token": settings.auth_token})
        statuses = [client.get("/api/state").status_code for _ in range(8)]

    assert statuses[0] == 200
    assert 429 in statuses
    assert statuses.count(200) <= 5


def test_a_rate_limited_response_tells_the_client_when_to_retry(
    settings: Settings, db: Database
) -> None:
    from nova.app import create_app

    throttled = settings.model_copy(update={"http_rate_limit": 1, "http_rate_window_s": 60.0})
    app = create_app(throttled, db=db)

    with TestClient(app, base_url=ORIGIN) as client:
        client.headers.update({"X-Nova-Token": settings.auth_token})
        client.get("/api/state")
        response = client.get("/api/state")

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1


# ------------------------------------------------------------- size limits
def test_oversized_json_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/todos",
        json={"text": "x" * 5000},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 422


def test_unknown_fields_are_refused(client: TestClient) -> None:
    response = client.post(
        "/api/todos",
        json={"text": "ok", "priority": "urgent"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 422


def test_audio_and_message_caps_have_sane_defaults(settings: Settings) -> None:
    assert settings.max_audio_seconds <= 600
    assert settings.max_audio_bytes >= 64_000
    assert settings.max_message_chars <= 100_000
    assert settings.max_ws_message_bytes >= 4096


def test_websocket_messages_are_rate_limited(client: TestClient) -> None:
    """A burst of control frames is answered with a rate-limit error, not work."""
    with client.websocket_connect("/ws", headers=WS_HEADERS) as socket:
        assert socket.receive_json()["type"] == "ready"
        kinds = []
        for _ in range(settings_ws_limit(client) + 5):
            socket.send_json({"type": "ping"})
            kinds.append(socket.receive_json())
    codes = {event.get("code") for event in kinds if event["type"] == "error"}
    assert "rate_limited" in codes


def settings_ws_limit(client: TestClient) -> int:
    state = client.app.state.nova
    return int(state.settings.ws_rate_limit)
