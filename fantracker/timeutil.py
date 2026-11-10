"""Reset boundaries. Everything is UTC, matching the game's own day change.

Daily resets at 00:00 UTC, weekly on Monday 00:00 UTC, monthly on the 1st
at 00:00 UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

PERIODS = ("daily", "weekly", "monthly")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not accepted; pass an aware datetime")
    return moment.astimezone(UTC)


def period_start_day(period: str, now: datetime) -> date:
    """First UTC calendar day of the period that contains `now`."""
    today = _as_utc(now).date()
    if period == "daily":
        return today
    if period == "weekly":
        return today - timedelta(days=today.weekday())  # Monday is 0
    if period == "monthly":
        return today.replace(day=1)
    raise ValueError(f"unknown period: {period!r}")


def next_reset(period: str, now: datetime) -> datetime:
    """The moment the current period ends and the next one begins."""
    start = period_start_day(period, now)
    if period == "daily":
        following = start + timedelta(days=1)
    elif period == "weekly":
        following = start + timedelta(days=7)
    else:
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return datetime(following.year, following.month, following.day, tzinfo=UTC)


def iso(moment: datetime) -> str:
    return _as_utc(moment).isoformat(timespec="seconds")


def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text)
