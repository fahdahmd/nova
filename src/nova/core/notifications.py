"""Best-effort desktop notifications.

The WebSocket toast in the UI is the primary delivery channel; an OS
notification is a bonus when the platform offers one. Text is handed to the
helper through the environment, never interpolated into a command line, so a
reminder body can never become part of a command. No shell is ever used.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess  # nosec B404 - fixed argv only, never a shell
from pathlib import Path

from nova.logging_setup import get_logger

log = get_logger(__name__)

_TITLE_ENV = "NOVA_NOTIFY_TITLE"
_BODY_ENV = "NOVA_NOTIFY_BODY"

# Reads its text from the environment, so nothing is interpolated into code.
_WINDOWS_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "$n = New-Object System.Windows.Forms.NotifyIcon;"
    "$n.Icon = [System.Drawing.SystemIcons]::Information;"
    "$n.BalloonTipTitle = $env:NOVA_NOTIFY_TITLE;"
    "$n.BalloonTipText = $env:NOVA_NOTIFY_BODY;"
    "$n.Visible = $true;"
    "$n.ShowBalloonTip(8000);"
    "Start-Sleep -Seconds 9;"
    "$n.Dispose()"
)

_MACOS_SCRIPT = (
    'display notification (system attribute "NOVA_NOTIFY_BODY") '
    'with title (system attribute "NOVA_NOTIFY_TITLE")'
)


def _spawn(argv: list[str], env_extra: dict[str, str]) -> bool:
    """Launch a detached helper process with a fixed argument vector."""
    env = {**os.environ, **env_extra}
    try:
        subprocess.Popen(  # noqa: S603  # nosec B603 - fixed argv, no shell
            argv,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            shell=False,
        )
    except (OSError, ValueError) as exc:
        log.debug("notify.spawn_failed", argv=argv[0], error=str(exc))
        return False
    return True


def notify(title: str, body: str) -> bool:
    """Show a desktop notification. Returns False when unavailable."""
    title = (title or "Reminder")[:80]
    body = (body or "")[:300]
    env_extra = {_TITLE_ENV: title, _BODY_ENV: body}

    system = platform.system()

    if system == "Windows":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return False
        return _spawn(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT],
            env_extra,
        )

    if system == "Darwin":
        osascript = shutil.which("osascript")
        if not osascript:
            return False
        return _spawn([osascript, "-e", _MACOS_SCRIPT], env_extra)

    notify_send = shutil.which("notify-send")
    if notify_send:
        # notify-send takes the text as plain arguments; no quoting involved.
        return _spawn([notify_send, "--app-name=Nova", title, body], env_extra)
    return False


def open_url(url: str) -> bool:
    """Open a local URL in the default browser, without a shell."""
    if not url.startswith(("http://127.0.0.1", "http://localhost")):
        raise ValueError("only loopback URLs may be opened")
    try:
        import webbrowser

        return webbrowser.open(url, new=2)
    except Exception as exc:  # pragma: no cover - platform dependent
        log.debug("browser.open_failed", error=str(exc))
        return False


def resolve_executable(argv: list[str]) -> list[str] | None:
    """Resolve argv[0] to a real executable path, or return None."""
    if not argv:
        return None
    program = argv[0]
    resolved = shutil.which(program)
    if resolved is None:
        candidate = Path(program)
        if candidate.is_file():
            resolved = str(candidate)
        else:
            return None
    return [resolved, *argv[1:]]
