"""Authentication, Origin/Host validation and security headers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nova.config import Settings
from nova.security.auth import check_host, check_origin, token_matches
from nova.security.headers import content_security_policy

from .conftest import ORIGIN, TEST_TOKEN


# ------------------------------------------------------------------- tokens
def test_token_comparison_accepts_only_the_exact_token() -> None:
    assert token_matches(TEST_TOKEN, TEST_TOKEN) is True
    assert token_matches(TEST_TOKEN + "x", TEST_TOKEN) is False
    assert token_matches(TEST_TOKEN[:-1], TEST_TOKEN) is False
    assert token_matches("", TEST_TOKEN) is False
    assert token_matches(None, TEST_TOKEN) is False


def test_token_comparison_survives_non_ascii() -> None:
    assert token_matches("tökén", TEST_TOKEN) is False


def test_generated_tokens_are_long_and_unique() -> None:
    first = Settings(allowed_roots=["."]).auth_token
    second = Settings(allowed_roots=["."]).auth_token
    assert first != second
    assert len(first) >= 32


def test_a_weak_token_is_refused_outright() -> None:
    with pytest.raises(ValueError, match="at least 24 characters"):
        Settings(auth_token="short", allowed_roots=["."])


# -------------------------------------------------------------------- hosts
def test_host_header_must_name_a_loopback_host(settings: Settings) -> None:
    assert check_host("127.0.0.1:8765", settings) is True
    assert check_host("localhost:8765", settings) is True
    assert check_host("127.0.0.1", settings) is True
    assert check_host("evil.example.com", settings) is False
    assert check_host("127.0.0.1.evil.com:8765", settings) is False
    assert check_host("127.0.0.1:9999", settings) is False
    assert check_host(None, settings) is False


def test_host_header_ignores_userinfo_tricks(settings: Settings) -> None:
    assert check_host("evil.com@127.0.0.1:8765", settings) is True
    assert check_host("127.0.0.1:8765@evil.com", settings) is False


# ------------------------------------------------------------------ origins
def test_origin_must_match_the_local_app(settings: Settings) -> None:
    assert check_origin(ORIGIN, settings, required=True) is True
    assert check_origin("http://localhost:8765", settings, required=True) is True
    assert check_origin("http://evil.example.com", settings, required=True) is False
    assert check_origin("https://127.0.0.1:8765", settings, required=True) is False
    assert check_origin("http://127.0.0.1:9999", settings, required=True) is False
    assert check_origin("null", settings, required=True) is False


def test_a_missing_origin_is_allowed_only_where_it_is_optional(settings: Settings) -> None:
    assert check_origin(None, settings, required=False) is True
    assert check_origin(None, settings, required=True) is False


# ------------------------------------------------------- loopback-only bind
def test_the_app_refuses_to_bind_beyond_loopback() -> None:
    for host in ["0.0.0.0", "192.168.1.10", "::"]:  # noqa: S104 - the point of the test
        with pytest.raises(ValueError, match="loopback"):
            Settings(host=host, allowed_roots=["."])

    assert Settings(host="127.0.0.1", allowed_roots=["."]).host == "127.0.0.1"


# ------------------------------------------------------------ http surface
def test_health_is_public(anon_client: TestClient) -> None:
    response = anon_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_requires_a_token(anon_client: TestClient) -> None:
    assert anon_client.get("/api/state").status_code == 401


def test_api_rejects_a_wrong_token(anon_client: TestClient) -> None:
    response = anon_client.get(
        "/api/state", headers={"X-Nova-Token": "wrong_token_wrong_token_wrong_1"}
    )
    assert response.status_code == 401


def test_api_accepts_the_token_in_a_header(client: TestClient) -> None:
    assert client.get("/api/state").status_code == 200


def test_api_accepts_the_token_in_a_cookie(anon_client: TestClient) -> None:
    anon_client.cookies.set("nova_session", TEST_TOKEN)
    assert anon_client.get("/api/state").status_code == 200


def test_index_without_a_token_explains_itself(anon_client: TestClient) -> None:
    response = anon_client.get("/")
    assert response.status_code == 401
    assert "Session token required" in response.text


def test_index_with_a_token_sets_an_httponly_cookie(anon_client: TestClient) -> None:
    response = anon_client.get(f"/?token={TEST_TOKEN}")
    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "")
    assert "nova_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie.replace("samesite", "SameSite")


def test_a_bad_host_header_is_rejected(client: TestClient) -> None:
    response = client.get("/api/state", headers={"Host": "evil.example.com"})
    assert response.status_code == 400


def test_cross_origin_writes_are_refused(client: TestClient) -> None:
    response = client.post(
        "/api/todos",
        json={"text": "sneaky"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert response.status_code == 403


def test_same_origin_writes_are_allowed(client: TestClient) -> None:
    response = client.post("/api/todos", json={"text": "legitimate"}, headers={"Origin": ORIGIN})
    assert response.status_code == 201


def test_static_assets_need_the_token(anon_client: TestClient) -> None:
    assert anon_client.get("/static/js/app.js").status_code == 401


def test_static_assets_resist_traversal(client: TestClient) -> None:
    for path in ["/static/../config.py", "/static/..%2fconfig.py", "/static/../../pyproject.toml"]:
        assert client.get(path).status_code in {307, 404}


def test_only_known_asset_types_are_served(client: TestClient, settings: Settings) -> None:
    from nova.api.routes import STATIC_DIR

    (STATIC_DIR / "notes.bin").write_bytes(b"secret")
    try:
        assert client.get("/static/notes.bin").status_code == 404
    finally:
        (STATIC_DIR / "notes.bin").unlink()


# ---------------------------------------------------------------- headers
def test_security_headers_are_present_on_every_response(client: TestClient) -> None:
    headers = client.get("/api/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "microphone=(self)" in headers["permissions-policy"]
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_the_csp_allows_no_inline_scripts_and_no_third_parties() -> None:
    policy = content_security_policy()
    assert "script-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy
    assert "default-src 'none'" in policy
    assert "https://" not in policy


def test_no_cors_headers_are_ever_sent(client: TestClient) -> None:
    headers = client.get("/api/state", headers={"Origin": ORIGIN}).headers
    assert "access-control-allow-origin" not in headers


def test_error_responses_never_leak_internals(anon_client: TestClient) -> None:
    body = anon_client.get("/api/state").text
    assert "Traceback" not in body
    assert "nova/" not in body
