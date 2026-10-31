"""Reminder tools: set with natural language times, list, cancel."""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import BaseModel, Field

from nova.core.timeparse import TimeParseError, humanise_delta, parse_future_time
from nova.tools.base import RiskTier, Tool, ToolContext, ToolResult, compact_json


class SetReminderArgs(BaseModel):
    model_config = {"extra": "forbid"}

    text: str = Field(min_length=1, max_length=300, description="What to be reminded about.")
    when: str = Field(
        min_length=1,
        max_length=100,
        description="When, in plain words: 'in 20 minutes', 'tomorrow 9am', 'friday at 6pm'.",
    )


class ListRemindersArgs(BaseModel):
    model_config = {"extra": "forbid"}

    include_past: bool = Field(default=False, description="Also include fired or cancelled ones.")


class CancelReminderArgs(BaseModel):
    model_config = {"extra": "forbid"}

    reminder_id: int = Field(ge=1, description="Numeric id of the reminder to cancel.")


class SetReminder(Tool):
    name: ClassVar[str] = "reminders_set"
    description: ClassVar[str] = (
        "Set a reminder for a future time. Accepts plain language like 'in 20 minutes'."
    )
    args_model: ClassVar[type[BaseModel]] = SetReminderArgs

    async def run(self, args: SetReminderArgs, context: ToolContext) -> ToolResult:
        try:
            due_at = parse_future_time(args.when)
        except TimeParseError as exc:
            return ToolResult.failure(str(exc))

        reminder = await asyncio.to_thread(context.db.add_reminder, args.text, due_at)
        if context.scheduler is not None:
            context.scheduler.schedule(reminder)

        when_text = due_at.strftime("%a %d %b at %H:%M")
        return ToolResult(
            ok=True,
            content=f"Reminder #{reminder.id} set for {when_text} ({humanise_delta(due_at)}).",
            data={"reminder": reminder.model_dump(mode="json")},
            refresh=("reminders",),
        )


class ListReminders(Tool):
    name: ClassVar[str] = "reminders_list"
    description: ClassVar[str] = "List reminders that have not fired yet."
    args_model: ClassVar[type[BaseModel]] = ListRemindersArgs

    async def run(self, args: ListRemindersArgs, context: ToolContext) -> ToolResult:
        reminders = await asyncio.to_thread(
            context.db.list_reminders, pending_only=not args.include_past, limit=50
        )
        if not reminders:
            return ToolResult(ok=True, content="There are no reminders set.")
        summary = [
            {
                "id": r.id,
                "text": r.text,
                "due": r.due_at.strftime("%a %d %b %H:%M"),
                "status": r.status,
            }
            for r in reminders
        ]
        return ToolResult(
            ok=True,
            content=compact_json(summary),
            data={"reminders": [r.model_dump(mode="json") for r in reminders]},
        )


class CancelReminder(Tool):
    name: ClassVar[str] = "reminders_cancel"
    description: ClassVar[str] = "Cancel a pending reminder by its numeric id."
    args_model: ClassVar[type[BaseModel]] = CancelReminderArgs
    risk: ClassVar[RiskTier] = RiskTier.DESTRUCTIVE

    def confirmation_prompt(self, args: BaseModel, context: ToolContext) -> str:
        reminder_id = getattr(args, "reminder_id", "?")
        reminder = (
            context.db.get_reminder(int(reminder_id)) if isinstance(reminder_id, int) else None
        )
        label = f'"{reminder.text}"' if reminder else f"#{reminder_id}"
        return f"Cancel reminder {label}?"

    async def run(self, args: CancelReminderArgs, context: ToolContext) -> ToolResult:
        if context.scheduler is not None:
            cancelled = context.scheduler.cancel(args.reminder_id)
        else:
            cancelled = await asyncio.to_thread(
                context.db.set_reminder_status, args.reminder_id, "cancelled"
            )
        if not cancelled:
            return ToolResult.failure(f"no pending reminder with id {args.reminder_id}")
        return ToolResult(
            ok=True,
            content=f"Cancelled reminder #{args.reminder_id}.",
            refresh=("reminders",),
        )
