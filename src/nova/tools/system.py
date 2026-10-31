"""Local system information: time, date, battery, CPU and memory."""

from __future__ import annotations

import asyncio
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

import psutil
from pydantic import BaseModel, Field

from nova.tools.base import RiskTier, Tool, ToolContext, ToolResult, compact_json

InfoKind = Literal["all", "time", "battery", "cpu", "memory", "disk", "platform"]


class SystemInfoArgs(BaseModel):
    model_config = {"extra": "forbid"}

    kind: InfoKind = Field(
        default="all",
        description="Which reading to take: all, time, battery, cpu, memory, disk or platform.",
    )


def _time_info() -> dict[str, Any]:
    now = datetime.now().astimezone()
    return {
        "iso": now.isoformat(timespec="seconds"),
        "time": now.strftime("%H:%M"),
        "date": now.strftime("%A %d %B %Y"),
        "timezone": now.strftime("%Z") or str(now.tzinfo),
    }


def _battery_info() -> dict[str, Any]:
    getter = getattr(psutil, "sensors_battery", None)
    battery = getter() if callable(getter) else None
    if battery is None:
        return {"present": False}
    remaining = battery.secsleft
    minutes: int | None = None
    if isinstance(remaining, int) and remaining not in (
        getattr(psutil, "POWER_TIME_UNLIMITED", -1),
        getattr(psutil, "POWER_TIME_UNKNOWN", -2),
    ):
        minutes = max(0, remaining // 60)
    return {
        "present": True,
        "percent": round(battery.percent),
        "plugged_in": bool(battery.power_plugged),
        "minutes_left": minutes,
    }


def _cpu_info() -> dict[str, Any]:
    return {
        "percent": psutil.cpu_percent(interval=0.3),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
    }


def _memory_info() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    gib = 1024**3
    return {
        "percent_used": memory.percent,
        "total_gb": round(memory.total / gib, 1),
        "available_gb": round(memory.available / gib, 1),
    }


def _disk_info() -> dict[str, Any]:
    # The home drive is the one the user actually cares about.
    mount = Path.home().anchor or "/"
    usage = psutil.disk_usage(mount)
    gib = 1024**3
    return {
        "mount": mount,
        "percent_used": usage.percent,
        "total_gb": round(usage.total / gib, 1),
        "free_gb": round(usage.free / gib, 1),
    }


def _platform_info() -> dict[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def _collect(kind: InfoKind) -> dict[str, Any]:
    readers = {
        "time": _time_info,
        "battery": _battery_info,
        "cpu": _cpu_info,
        "memory": _memory_info,
        "disk": _disk_info,
        "platform": _platform_info,
    }
    if kind != "all":
        return {kind: readers[kind]()}
    result: dict[str, Any] = {}
    for name, reader in readers.items():
        try:
            result[name] = reader()
        except (OSError, RuntimeError, ValueError) as exc:
            result[name] = {"error": str(exc)}
    return result


class SystemInfo(Tool):
    name: ClassVar[str] = "system_info"
    description: ClassVar[str] = (
        "Read local system state: current time and date, battery, CPU, memory, disk."
    )
    args_model: ClassVar[type[BaseModel]] = SystemInfoArgs
    risk: ClassVar[RiskTier] = RiskTier.SAFE

    async def run(self, args: SystemInfoArgs, context: ToolContext) -> ToolResult:
        del context
        data = await asyncio.to_thread(_collect, args.kind)
        return ToolResult(ok=True, content=compact_json(data), data={"system": data})
