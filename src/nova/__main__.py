"""Command line entry point: ``python -m nova``.

Prints the one-click tokenized URL, performs friendly pre-flight checks, and
runs uvicorn bound to loopback only.
"""

from __future__ import annotations

import argparse
import sys
import threading

import uvicorn

from nova import __version__
from nova.app import create_app
from nova.config import Settings, get_settings
from nova.core.notifications import open_url
from nova.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

BANNER = """
  {name} {version} - local voice and text assistant
  {rule}
  Open this link (it carries your session token):

      {url}

  Model      {model} via {ollama}
  Voice      {voice}
  Speech     {stt}
  Folders    {roots}
  Apps       {apps}
  Database   {db}

  Press Ctrl+C to stop.
"""


def banner(settings: Settings, *, voice_ready: bool) -> str:
    roots = ", ".join(str(root) for root in settings.allowed_roots) or "none"
    apps = ", ".join(sorted(settings.allowed_apps)) or "none"
    return BANNER.format(
        name=settings.app_name,
        version=__version__,
        rule="-" * 58,
        url=settings.tokenized_url(),
        model=settings.ollama_model,
        ollama=settings.ollama_host,
        voice=(
            settings.piper_voice_path.name
            if voice_ready
            else "not installed (run scripts/download_voice.py)"
        ),
        stt=f"faster-whisper {settings.whisper_model}" if settings.stt_enabled else "disabled",
        roots=roots,
        apps=apps,
        db=settings.db_path,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="nova", description="Run the Nova assistant.")
    parser.add_argument("--host", help="loopback address to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port to listen on (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--reload", action="store_true", help="auto-reload (development)")
    parser.add_argument("--version", action="version", version=f"nova {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        settings = get_settings()
    except Exception as exc:  # configuration errors must be readable
        print(f"Configuration error:\n  {exc}", file=sys.stderr)
        return 2

    overrides: dict[str, object] = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    if overrides:
        try:
            settings = settings.model_copy(update=overrides)
            Settings.model_validate(settings.model_dump())
        except Exception as exc:
            print(f"Invalid option:\n  {exc}", file=sys.stderr)
            return 2

    configure_logging(
        settings.log_level,
        json_logs=settings.log_json,
        log_file=str(settings.log_file) if settings.log_file else None,
    )

    print(banner(settings, voice_ready=settings.piper_voice_path.exists()), flush=True)

    if settings.open_browser and not args.no_browser:
        # Give the server a moment to bind before the browser knocks.
        threading.Timer(1.2, lambda: open_url(settings.tokenized_url())).start()

    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
        ws_max_size=settings.max_ws_message_bytes * 2,
        timeout_graceful_shutdown=5,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
