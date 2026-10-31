"""Reminder scheduling on top of APScheduler.

Pending reminders are reloaded from SQLite at startup, so restarting Nova never
loses one. When a reminder fires it is pushed to every connected UI over the
WebSocket and, where the platform allows, shown as an OS notification.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from nova.core.notifications import notify
from nova.db.database import Database
from nova.db.models import Reminder
from nova.logging_setup import get_logger

log = get_logger(__name__)

FireCallback = Callable[[Reminder], Awaitable[None]]


def _job_id(reminder_id: int) -> str:
    return f"reminder:{reminder_id}"


class ReminderScheduler:
    """Owns the APScheduler instance and the reminder lifecycle."""

    def __init__(self, db: Database, app_name: str = "Nova") -> None:
        self._db = db
        self._app_name = app_name
        self._scheduler = AsyncIOScheduler(timezone=datetime.now().astimezone().tzinfo)
        self._on_fire: FireCallback | None = None
        self._started = False

    # ------------------------------------------------------------ lifecycle
    def set_callback(self, callback: FireCallback) -> None:
        """Register the coroutine used to push a fired reminder to the UI."""
        self._on_fire = callback

    def start(self) -> None:
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        restored = self.restore_pending()
        log.info("scheduler.started", restored=restored)

    def shutdown(self) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=False)
        self._started = False
        log.info("scheduler.stopped")

    @property
    def running(self) -> bool:
        return self._started

    # ------------------------------------------------------------ scheduling
    def restore_pending(self) -> int:
        """Re-arm reminders that survived a restart; fire overdue ones now."""
        count = 0
        now = datetime.now(UTC)
        for reminder in self._db.list_reminders(pending_only=True, limit=500):
            if reminder.due_at <= now:
                # Overdue while Nova was down: deliver it a few seconds from now.
                self._add_job(reminder, when=now)
            else:
                self._add_job(reminder)
            count += 1
        return count

    def schedule(self, reminder: Reminder) -> None:
        """Arm a newly created reminder."""
        self._add_job(reminder)
        log.info("reminder.scheduled", reminder_id=reminder.id, due_at=reminder.due_at.isoformat())

    def _add_job(self, reminder: Reminder, *, when: datetime | None = None) -> None:
        self._scheduler.add_job(
            self._fire,
            trigger=DateTrigger(run_date=when or reminder.due_at),
            args=[reminder.id],
            id=_job_id(reminder.id),
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
        )

    def cancel(self, reminder_id: int) -> bool:
        """Cancel a pending reminder and drop its job."""
        changed = self._db.set_reminder_status(reminder_id, "cancelled")
        with suppress(JobLookupError):
            self._scheduler.remove_job(_job_id(reminder_id))
        if changed:
            log.info("reminder.cancelled", reminder_id=reminder_id)
        return changed

    # --------------------------------------------------------------- firing
    async def _fire(self, reminder_id: int) -> None:
        reminder = self._db.get_reminder(reminder_id)
        if reminder is None or reminder.status != "pending":
            return

        self._db.set_reminder_status(reminder_id, "fired")
        fired = reminder.model_copy(update={"status": "fired"})
        log.info("reminder.fired", reminder_id=reminder_id)

        notify(f"{self._app_name} reminder", reminder.text)

        if self._on_fire is not None:
            try:
                await self._on_fire(fired)
            except Exception as exc:  # pragma: no cover - UI push must not crash the job
                log.warning("reminder.push_failed", reminder_id=reminder_id, error=str(exc))
