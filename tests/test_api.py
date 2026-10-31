"""HTTP API behaviour and front-end safety invariants."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from nova.api.routes import STATIC_DIR
from nova.db.database import Database

from .conftest import ORIGIN, WS_HEADERS

JSON_ORIGIN = {"Origin": ORIGIN}


# -------------------------------------------------------------- state shape
def test_state_describes_everything_the_ui_needs(client: TestClient) -> None:
    payload = client.get("/api/state").json()
    for key in ["notes", "todos", "reminders", "memory", "audit", "preferences", "config"]:
        assert key in payload

    config = payload["config"]
    for key in ["app_name", "model", "voice_available", "allowed_roots", "allowed_apps", "tools"]:
        assert key in config
    assert config["tools"]


def test_state_never_contains_the_session_token(client: TestClient) -> None:
    assert "test_token" not in client.get("/api/state").text


# ------------------------------------------------------------------- todos
def test_todo_lifecycle(client: TestClient) -> None:
    created = client.post("/api/todos", json={"text": "write the readme"}, headers=JSON_ORIGIN)
    assert created.status_code == 201
    todo_id = created.json()["id"]

    assert client.post(f"/api/todos/{todo_id}/complete", headers=JSON_ORIGIN).status_code == 204
    # Completing twice is a no-op, not a crash.
    assert client.post(f"/api/todos/{todo_id}/complete", headers=JSON_ORIGIN).status_code == 404
    assert client.delete(f"/api/todos/{todo_id}", headers=JSON_ORIGIN).status_code == 204
    assert client.delete(f"/api/todos/{todo_id}", headers=JSON_ORIGIN).status_code == 404


def test_note_lifecycle(client: TestClient) -> None:
    created = client.post(
        "/api/notes", json={"title": "Keys", "body": "blue tin"}, headers=JSON_ORIGIN
    )
    assert created.status_code == 201
    note_id = created.json()["id"]

    notes = client.get("/api/state").json()["notes"]
    assert any(note["id"] == note_id for note in notes)
    assert client.delete(f"/api/notes/{note_id}", headers=JSON_ORIGIN).status_code == 204


def test_memory_can_be_inspected_and_cleared(client: TestClient, db: Database) -> None:
    db.remember("Drinks tea, not coffee")
    assert len(client.get("/api/state").json()["memory"]) == 1

    assert client.delete("/api/memory", headers=JSON_ORIGIN).json() == {"removed": 1}
    assert client.get("/api/state").json()["memory"] == []


def test_conversation_history_round_trips(client: TestClient, db: Database) -> None:
    db.add_message("user", "hello")
    db.add_message("assistant", "hi there")

    messages = client.get("/api/messages").json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]

    assert client.delete("/api/messages", headers=JSON_ORIGIN).json() == {"removed": 2}
    assert client.get("/api/messages").json()["messages"] == []


def test_preferences_persist(client: TestClient) -> None:
    assert (
        client.post(
            "/api/preferences", json={"key": "theme", "value": "light"}, headers=JSON_ORIGIN
        ).status_code
        == 204
    )
    assert client.get("/api/state").json()["preferences"]["theme"] == "light"


def test_a_malformed_preference_key_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/preferences", json={"key": "THEME; drop table", "value": "x"}, headers=JSON_ORIGIN
    )
    assert response.status_code == 422


def test_unknown_ids_produce_clean_404s(client: TestClient) -> None:
    for path in ["/api/notes/9999", "/api/todos/9999", "/api/reminders/9999", "/api/memory/9999"]:
        response = client.delete(path, headers=JSON_ORIGIN)
        assert response.status_code == 404
        assert "Traceback" not in response.text


def test_sql_injection_in_a_payload_is_stored_as_plain_text(
    client: TestClient, db: Database
) -> None:
    """Parameterised SQL everywhere means this is just a funny-looking string."""
    nasty = "'); DROP TABLE todos; --"
    assert client.post("/api/todos", json={"text": nasty}, headers=JSON_ORIGIN).status_code == 201
    assert [todo.text for todo in db.list_todos()] == [nasty]


# --------------------------------------------------------------- the page
def test_the_index_renders_with_the_configured_name(client: TestClient) -> None:
    body = client.get("/").text
    assert "{{APP_NAME}}" not in body
    assert "<title>Nova</title>" in body


def test_the_page_has_no_inline_scripts_or_remote_assets() -> None:
    """The CSP forbids both; this keeps the markup honest."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html
    assert "onclick=" not in html
    assert "http://" not in html.replace("http://127.0.0.1:8765", "")
    assert "https://" not in html
    assert 'src="/static/js/app.js"' in html


def strip_comments(source: str) -> str:
    """Remove // and /* */ comments so prose cannot trip a code scan."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)


def test_no_script_sets_innerhtml() -> None:
    """Model output is rendered as DOM nodes, never parsed as markup."""
    for script in sorted(Path(STATIC_DIR / "js").glob("*.js")):
        source = strip_comments(script.read_text(encoding="utf-8"))
        assert "innerHTML" not in source, script.name
        assert "outerHTML" not in source, script.name
        assert "insertAdjacentHTML" not in source, script.name
        assert "document.write" not in source, script.name
        assert "eval(" not in source, script.name


def test_the_stylesheet_pulls_in_nothing_remote() -> None:
    css = (STATIC_DIR / "css" / "styles.css").read_text(encoding="utf-8")
    assert "@import" not in css
    assert "https://" not in css


# ------------------------------------------------------------- websocket
def test_the_socket_announces_what_it_can_do(client: TestClient) -> None:
    with client.websocket_connect("/ws", headers=WS_HEADERS) as socket:
        ready = socket.receive_json()

    assert ready["type"] == "ready"
    assert ready["app_name"] == "Nova"
    assert ready["model"]
    assert ready["tools"]
    assert ready["stt_available"] is False  # disabled in the test settings
    assert ready["voice_available"] is False


@pytest.mark.parametrize(
    ("label", "headers"),
    [
        ("no origin", {key: v for key, v in WS_HEADERS.items() if key != "Origin"}),
        ("foreign origin", {**WS_HEADERS, "Origin": "http://evil.example.com"}),
        ("bad token", {**WS_HEADERS, "X-Nova-Token": "nope_nope_nope_nope_nope_nope"}),
        ("no token", {key: v for key, v in WS_HEADERS.items() if key != "X-Nova-Token"}),
        ("foreign host", {**WS_HEADERS, "Host": "evil.example.com"}),
    ],
)
def test_the_socket_refuses_an_unauthorised_handshake(
    anon_client: TestClient, label: str, headers: dict[str, str]
) -> None:
    """Every one of these is turned away before the upgrade completes.

    ``anon_client`` sends no default headers, so a header left out of the
    parameters really is absent from the handshake.
    """
    with (
        pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
        anon_client.websocket_connect("/ws", headers=headers),
    ):
        pass


def test_a_fully_authorised_handshake_succeeds(anon_client: TestClient) -> None:
    with anon_client.websocket_connect("/ws", headers=WS_HEADERS) as socket:
        assert socket.receive_json()["type"] == "ready"


def test_audio_without_speech_support_is_explained(client: TestClient) -> None:
    with client.websocket_connect("/ws", headers=WS_HEADERS) as socket:
        socket.receive_json()
        socket.send_json({"type": "audio_start", "mode": "push_to_talk", "sample_rate": 16000})
        event = socket.receive_json()

    assert event["type"] == "error"
    assert event["code"] == "stt_disabled"
    assert "NOVA_STT_ENABLED" in event["hint"]
