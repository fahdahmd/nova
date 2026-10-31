"""Shared fixtures.

Tests run entirely offline: speech is disabled so no model is ever downloaded,
and the LLM is replaced with a scripted fake wherever a turn is exercised.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nova.app import create_app
from nova.config import AppCommand, Settings
from nova.db.database import Database
from nova.security.sandbox import PathSandbox
from nova.tools import build_registry
from nova.tools.base import ToolContext

TEST_TOKEN = "test_token_test_token_test_token_1234"
ORIGIN = "http://127.0.0.1:8765"
HOST = "127.0.0.1:8765"

# Starlette's TestClient sends "Host: testserver" and no Origin on a WebSocket
# upgrade, so a handshake that should succeed has to spell both out.
WS_HEADERS = {"Host": HOST, "Origin": ORIGIN, "X-Nova-Token": TEST_TOKEN}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop a developer's real .env from leaking into the tests."""
    for key in list(os.environ):
        if key.startswith("NOVA_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A sandbox root with a small, realistic tree."""
    root = tmp_path / "workspace"
    (root / "projects" / "alpha").mkdir(parents=True)
    (root / "notes").mkdir()

    (root / "readme.md").write_text("# Hello\nSome text.\n", encoding="utf-8")
    (root / "notes" / "groceries.txt").write_text("milk\neggs\n", encoding="utf-8")
    (root / "projects" / "alpha" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    (root / "id_rsa").write_text("PRIVATE KEY\n", encoding="utf-8")
    (root / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0binary")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "secret.txt").write_text("nope\n", encoding="utf-8")
    return root


@pytest.fixture
def secret_dir(tmp_path: Path) -> Path:
    """A directory deliberately outside every sandbox root."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "passwords.txt").write_text("hunter2\n", encoding="utf-8")
    return outside


@pytest.fixture
def second_root(tmp_path: Path) -> Path:
    """A second allowed root, used to test the safe/sensitive distinction."""
    root = tmp_path / "secondary"
    root.mkdir()
    (root / "archive.md").write_text("old stuff\n", encoding="utf-8")
    return root


@pytest.fixture
def settings(tmp_path: Path, workspace: Path, second_root: Path) -> Settings:
    return Settings(
        auth_token=TEST_TOKEN,
        app_name="Nova",
        port=8765,
        db_path=tmp_path / "nova.db",
        allowed_roots=[workspace, second_root],
        allowed_apps={
            "notepad": AppCommand(label="Notepad", argv=["notepad.exe"]),
        },
        stt_enabled=False,
        tts_enabled=False,
        open_browser=False,
        weather_enabled=True,
        web_search_enabled=True,
        max_file_bytes=4096,
        http_rate_limit=1000,
        log_level="WARNING",
    )


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    database = Database(settings.db_path)
    yield database
    database.close()


@pytest.fixture
def sandbox(settings: Settings) -> PathSandbox:
    return PathSandbox(roots=tuple(settings.allowed_roots), max_bytes=settings.max_file_bytes)


@pytest.fixture
def tool_context(settings: Settings, db: Database, sandbox: PathSandbox) -> ToolContext:
    return ToolContext(settings=settings, db=db, sandbox=sandbox, scheduler=None)


@pytest.fixture
def registry() -> object:
    return build_registry()


@pytest.fixture
def client(settings: Settings, db: Database) -> Iterator[TestClient]:
    """A TestClient with the lifespan running (scheduler started)."""
    app = create_app(settings, db=db)
    with TestClient(app, base_url=ORIGIN) as test_client:
        test_client.headers.update({"X-Nova-Token": TEST_TOKEN})
        yield test_client


@pytest.fixture
def anon_client(settings: Settings, db: Database) -> Iterator[TestClient]:
    """A TestClient that sends no credentials."""
    app = create_app(settings, db=db)
    with TestClient(app, base_url=ORIGIN) as test_client:
        yield test_client
