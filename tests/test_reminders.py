"""Natural-language reminder times, scheduling and persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from nova.core.timeparse import (
    MAX_HORIZON,
    TimeParseError,
    humanise_delta,
    parse_future_time,
)
from nova.db.database import Database
from nova.tools import build_registry
from nova.tools.base import ToolContext

# A Saturday afternoon, so weekday arithmetic is checkable.
NOW = datetime(2026, 10, 31, 15, 40, tzinfo=UTC)


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("in 20 minutes", datetime(2026, 10, 31, 16, 0, tzinfo=UTC)),
        ("20 minutes", datetime(2026, 10, 31, 16, 0, tzinfo=UTC)),
        ("in 45 seconds", datetime(2026, 10, 31, 15, 40, 45, tzinfo=UTC)),
        ("in 2 hours", datetime(2026, 10, 31, 17, 40, tzinfo=UTC)),
        ("in 3 days", datetime(2026, 11, 3, 15, 40, tzinfo=UTC)),
        ("2 weeks", datetime(2026, 11, 14, 15, 40, tzinfo=UTC)),
        ("tomorrow 9am", datetime(2026, 11, 1, 9, 0, tzinfo=UTC)),
        ("today 11pm", datetime(2026, 10, 31, 23, 0, tzinfo=UTC)),
        ("next monday 10:30", datetime(2026, 11, 2, 10, 30, tzinfo=UTC)),
        ("friday at 6pm", datetime(2026, 11, 6, 18, 0, tzinfo=UTC)),
        ("this friday at 6pm", datetime(2026, 11, 6, 18, 0, tzinfo=UTC)),
    ],
)
def test_understands_everyday_phrases(phrase: str, expected: datetime) -> None:
    parsed = parse_future_time(phrase, now=NOW)
    assert parsed.replace(microsecond=0) == expected


def test_next_weekday_skips_the_current_week() -> None:
    """Said on a Saturday, "next sunday" means the Sunday after tomorrow's."""
    this_sunday = parse_future_time("sunday", now=NOW)
    next_sunday = parse_future_time("next sunday", now=NOW)
    assert this_sunday.date() == datetime(2026, 11, 1, tzinfo=UTC).date()
    assert next_sunday.date() == datetime(2026, 11, 8, tzinfo=UTC).date()


@pytest.mark.parametrize(
    "phrase",
    ["yesterday", "last tuesday", "an hour ago", "in -5 minutes"],
)
def test_refuses_times_in_the_past(phrase: str) -> None:
    with pytest.raises(TimeParseError):
        parse_future_time(phrase, now=NOW)


@pytest.mark.parametrize("phrase", ["", "   ", "soonish", "qwertyuiop", "x" * 150])
def test_refuses_phrases_it_cannot_understand(phrase: str) -> None:
    with pytest.raises(TimeParseError):
        parse_future_time(phrase, now=NOW)


def test_refuses_absurdly_distant_times() -> None:
    with pytest.raises(TimeParseError, match="two years"):
        parse_future_time("in 2000 days", now=NOW)
    # The boundary itself is still acceptable.
    assert parse_future_time("in 300 days", now=NOW) < NOW + MAX_HORIZON


def test_results_are_always_timezone_aware() -> None:
    assert parse_future_time("in 10 minutes", now=NOW).tzinfo is not None
    assert parse_future_time("tomorrow 8am", now=NOW).tzinfo is not None


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=30), "in 30 seconds"),
        (timedelta(seconds=1), "in 1 second"),
        (timedelta(minutes=1), "in 1 minute"),
        (timedelta(minutes=25), "in 25 minutes"),
        (timedelta(hours=2), "in 2 hours"),
        (timedelta(days=3), "in 3 days"),
        (timedelta(days=1), "in 1 day"),
    ],
)
def test_describes_the_wait_in_plain_words(delta: timedelta, expected: str) -> None:
    assert humanise_delta(NOW + delta, now=NOW) == expected


def test_a_past_time_reads_as_now() -> None:
    assert humanise_delta(NOW - timedelta(minutes=5), now=NOW) == "now"


# ---------------------------------------------------------------- the tool
def test_the_tool_stores_a_reminder(tool_context: ToolContext) -> None:
    tool = build_registry().get("reminders_set")
    assert tool is not None
    args = tool.parse_args({"text": "stretch", "when": "in 25 minutes"})
    result = asyncio.run(tool.run(args, tool_context))

    assert result.ok is True
    assert "reminders" in result.refresh
    stored = tool_context.db.list_reminders()
    assert [item.text for item in stored] == ["stretch"]
    assert stored[0].due_at > datetime.now(UTC)


def test_the_tool_explains_an_unparseable_time(tool_context: ToolContext) -> None:
    tool = build_registry().get("reminders_set")
    assert tool is not None
    args = tool.parse_args({"text": "stretch", "when": "whenever"})
    result = asyncio.run(tool.run(args, tool_context))

    assert result.ok is False
    assert "could not understand" in result.content
    assert tool_context.db.list_reminders() == []


def test_cancelling_marks_the_reminder_cancelled(tool_context: ToolContext) -> None:
    registry = build_registry()
    create = registry.get("reminders_set")
    cancel = registry.get("reminders_cancel")
    assert create is not None and cancel is not None

    asyncio.run(create.run(create.parse_args({"text": "call bank", "when": "in 1 hour"}),
                           tool_context))
    reminder = tool_context.db.list_reminders()[0]

    result = asyncio.run(
        cancel.run(cancel.parse_args({"reminder_id": reminder.id}), tool_context)
    )
    assert result.ok is True
    assert tool_context.db.get_reminder(reminder.id).status == "cancelled"
    assert tool_context.db.list_reminders(pending_only=True) == []


def test_cancelling_an_unknown_reminder_fails_cleanly(tool_context: ToolContext) -> None:
    cancel = build_registry().get("reminders_cancel")
    assert cancel is not None
    result = asyncio.run(cancel.run(cancel.parse_args({"reminder_id": 4242}), tool_context))
    assert result.ok is False
    assert "no pending reminder" in result.content


# ------------------------------------------------------------- persistence
def test_pending_reminders_survive_a_restart(db: Database) -> None:
    """The scheduler re-arms whatever is still pending in the database."""
    from nova.core.scheduler import ReminderScheduler

    db.add_reminder("water plants", datetime.now(UTC) + timedelta(hours=2))
    db.add_reminder("already done", datetime.now(UTC) + timedelta(hours=3))
    db.set_reminder_status(2, "fired")

    scheduler = ReminderScheduler(db)
    restored = scheduler.restore_pending()
    assert restored == 1


def test_an_invalid_status_is_refused(db: Database) -> None:
    db.add_reminder("x", datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(ValueError, match="invalid reminder status"):
        db.set_reminder_status(1, "exploded")
