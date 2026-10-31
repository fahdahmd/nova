"""Launch whitelisted applications.

Only ids present in ``NOVA_ALLOWED_APPS`` can be launched, each mapped to a
fixed argument vector. No shell is involved and no argument ever comes from the
model - the model only picks an id.
"""

from __future__ import annotations

import asyncio
import subprocess  # nosec B404 - fixed argv from the config whitelist, never a shell
from typing import ClassVar

from pydantic import BaseModel, Field

from nova.core.notifications import resolve_executable
from nova.logging_setup import get_logger
from nova.tools.base import RiskTier, Tool, ToolContext, ToolResult, compact_json

log = get_logger(__name__)


class OpenAppArgs(BaseModel):
    model_config = {"extra": "forbid"}

    app: str = Field(
        min_length=1,
        max_length=64,
        description="Id of a whitelisted app. Call list_apps first if unsure.",
    )


def _launch(argv: list[str]) -> None:
    """Start a detached process from a fixed argument vector."""
    subprocess.Popen(  # noqa: S603  # nosec B603 - argv from the config whitelist
        argv,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


class ListApps(Tool):
    name: ClassVar[str] = "apps_list"
    description: ClassVar[str] = "List the applications the assistant may launch."
    risk: ClassVar[RiskTier] = RiskTier.SAFE

    async def run(self, args: BaseModel, context: ToolContext) -> ToolResult:
        del args
        apps = [
            {"app": key, "label": command.label}
            for key, command in context.settings.allowed_apps.items()
        ]
        if not apps:
            return ToolResult(ok=True, content="No applications are whitelisted.")
        return ToolResult(ok=True, content=compact_json(apps), data={"apps": apps})


class OpenApp(Tool):
    name: ClassVar[str] = "open_app"
    description: ClassVar[str] = "Open a whitelisted application by its id."
    args_model: ClassVar[type[BaseModel]] = OpenAppArgs
    risk: ClassVar[RiskTier] = RiskTier.SENSITIVE

    def confirmation_prompt(self, args: BaseModel, context: ToolContext) -> str:
        app_id = str(getattr(args, "app", "")).strip().lower()
        command = context.settings.allowed_apps.get(app_id)
        label = command.label if command else app_id
        return f"Open {label} on your computer?"

    async def run(self, args: OpenAppArgs, context: ToolContext) -> ToolResult:
        app_id = args.app.strip().lower()
        command = context.settings.allowed_apps.get(app_id)
        if command is None:
            available = ", ".join(sorted(context.settings.allowed_apps)) or "none"
            return ToolResult.failure(f"{args.app!r} is not whitelisted. Available: {available}")

        argv = resolve_executable(list(command.argv))
        if argv is None:
            return ToolResult.failure(f"{command.label} is not installed on this system")

        try:
            await asyncio.to_thread(_launch, argv)
        except (OSError, ValueError) as exc:
            log.warning("open_app.failed", app=app_id, error=str(exc))
            return ToolResult.failure(f"could not start {command.label}")

        return ToolResult(
            ok=True,
            content=f"Opened {command.label}.",
            data={"app": app_id, "label": command.label},
        )
