"""Natural-language time parsing for reminders.

Wraps ``dateparser`` with the settings that matter for a reminder: dates are
read as future dates, results are timezone-aware, and anything in the past or
absurdly far away is rejected with a message the model can act on.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import dateparser

MAX_HORIZON = timedelta(days=365 * 2)
MIN_LEAD = timedelta(seconds=5)

# dateparser handles "in 20 minutes", but these shorthands are common in speech
# and transcription often drops the word "in".
_SHORTHAND = re.compile(
    r"^(?:in\s+)?(?P<amount>\d{1,4})\s*(?P<unit>s|sec|secs|second|seconds|m|min|mins|minute|"
    r"minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$",
    re.IGNORECASE,
)

_UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
}


_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "mon",
    "tue",
    "tues",
    "wed",
    "thu",
    "thur",
    "thurs",
    "fri",
    "sat",
    "sun",
)

# Trailing clock time, e.g. "... 10:30", "... at 6pm", "... 9 am".
_TIME_TAIL = re.compile(
    r"(?:\s+at)?\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?\.?$",
    re.IGNORECASE,
)

_DATEPARSER_SETTINGS: dict[str, object] = {"PREFER_DATES_FROM": "future"}

_DAY_ALTERNATION = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
# dateparser does not understand "next monday"/"this friday", only "monday".
_NEXT_WEEKDAY = re.compile(rf"\bnext\s+(?P<day>{_DAY_ALTERNATION})\b", re.IGNORECASE)
_BARE_QUALIFIER = re.compile(
    rf"\b(?:this|coming|on)\s+(?P<day>{_DAY_ALTERNATION})\b", re.IGNORECASE
)


def _normalise(raw: str) -> tuple[str, bool]:
    """Rewrite weekday qualifiers dateparser cannot read.

    Returns the cleaned phrase and whether "next <weekday>" was used, which
    means the occurrence after the current week.
    """
    match = _NEXT_WEEKDAY.search(raw)
    if match is not None:
        return _NEXT_WEEKDAY.sub(lambda m: m.group("day"), raw, count=1), True
    return _BARE_QUALIFIER.sub(lambda m: m.group("day"), raw, count=1), False


class TimeParseError(ValueError):
    """Raised when a time expression cannot be understood or is not usable."""


def _split_date_and_time(raw: str, base: datetime) -> datetime | None:
    """Fallback for "<date phrase> <clock time>", which dateparser often misses.

    The date half and the clock half are parsed separately and recombined.
    """
    match = _TIME_TAIL.search(raw)
    if match is None:
        return None
    date_part = raw[: match.start()].strip().rstrip(",")
    if not date_part:
        return None

    parsed_date = dateparser.parse(
        date_part,
        settings={**_DATEPARSER_SETTINGS, "RELATIVE_BASE": base.replace(tzinfo=None)},
    )
    if parsed_date is None:
        return None

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = (match.group("ampm") or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None

    combined = parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if combined.tzinfo is None:
        combined = combined.replace(tzinfo=base.tzinfo)

    if combined <= base:
        # "monday 10:30" said on a Monday afternoon means the next Monday.
        is_weekday_phrase = any(word in date_part.lower() for word in _WEEKDAYS)
        combined += timedelta(days=7 if is_weekday_phrase else 1)
    return combined


def parse_future_time(text: str, *, now: datetime | None = None) -> datetime:
    """Turn a phrase like "in 20 minutes" or "tomorrow 9am" into a datetime.

    The result is timezone-aware and guaranteed to be in the future.
    """
    raw = (text or "").strip()
    if not raw:
        raise TimeParseError("no time was given")
    if len(raw) > 100:
        raise TimeParseError("the time expression is too long")

    base = now or datetime.now().astimezone()
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)

    raw, next_week = _normalise(raw)

    shorthand = _SHORTHAND.match(raw)
    if shorthand:
        amount = int(shorthand.group("amount"))
        seconds = _UNIT_SECONDS[shorthand.group("unit").lower()]
        due = base + timedelta(seconds=amount * seconds)
    else:
        # Parsed naively against the caller's clock, then stamped with the
        # caller's offset. Passing TIMEZONE instead would require a tz database
        # name, which Windows does not provide.
        parsed = dateparser.parse(
            raw,
            settings={**_DATEPARSER_SETTINGS, "RELATIVE_BASE": base.replace(tzinfo=None)},
        )
        if parsed is None:
            combined = _split_date_and_time(raw, base)
            if combined is None:
                raise TimeParseError(
                    f"could not understand the time {raw!r}; try 'in 20 minutes' or 'tomorrow 9am'"
                )
            parsed = combined
        due = (
            parsed.replace(tzinfo=base.tzinfo)
            if parsed.tzinfo is None
            else parsed.astimezone(base.tzinfo)
        )
        if next_week and due.isocalendar()[:2] == base.isocalendar()[:2]:
            due += timedelta(days=7)

    if due - base < MIN_LEAD:
        raise TimeParseError(f"{raw!r} resolves to the past; give a time in the future")
    if due - base > MAX_HORIZON:
        raise TimeParseError("reminders can be set at most two years ahead")
    return due


def humanise_delta(due: datetime, *, now: datetime | None = None) -> str:
    """Render the wait until ``due`` as a short human phrase."""
    base = now or datetime.now().astimezone()
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    seconds = int((due - base).total_seconds())
    if seconds < 0:
        return "now"
    if seconds < 60:
        return f"in {seconds} second{'s' if seconds != 1 else ''}"
    minutes = seconds // 60
    if minutes < 60:
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 24:
        remainder = minutes % 60
        tail = f" {remainder} min" if remainder else ""
        return f"in {hours} hour{'s' if hours != 1 else ''}{tail}"
    days = hours // 24
    return f"in {days} day{'s' if days != 1 else ''}"
