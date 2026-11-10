from datetime import date, datetime

import pytest

from fantracker.timeutil import next_reset, period_start_day
from tests.conftest import utc


@pytest.mark.parametrize(
    ("now", "daily", "weekly", "monthly"),
    [
        ("2026-11-04 12:00", date(2026, 11, 4), date(2026, 11, 2), date(2026, 11, 1)),  # Wednesday
        ("2026-11-08 23:59", date(2026, 11, 8), date(2026, 11, 2), date(2026, 11, 1)),  # Sunday, last minute
        ("2026-11-09 00:00", date(2026, 11, 9), date(2026, 11, 9), date(2026, 11, 1)),  # Monday, first minute
        ("2026-12-01 00:00", date(2026, 12, 1), date(2026, 11, 30), date(2026, 12, 1)),  # week spans a month end
    ],
)
def test_period_starts(now, daily, weekly, monthly):
    moment = utc(now)
    assert period_start_day("daily", moment) == daily
    assert period_start_day("weekly", moment) == weekly
    assert period_start_day("monthly", moment) == monthly


@pytest.mark.parametrize(
    ("now", "period", "expected"),
    [
        ("2026-11-04 12:00", "daily", "2026-11-05 00:00"),
        ("2026-11-04 12:00", "weekly", "2026-11-09 00:00"),
        ("2026-11-09 00:00", "weekly", "2026-11-16 00:00"),  # exactly at the reset: the next one
        ("2026-11-04 12:00", "monthly", "2026-12-01 00:00"),
        ("2026-12-31 23:59", "monthly", "2027-01-01 00:00"),
        ("2026-12-31 23:59", "daily", "2027-01-01 00:00"),
    ],
)
def test_next_reset(now, period, expected):
    assert next_reset(period, utc(now)) == utc(expected)


def test_non_utc_input_is_converted():
    from datetime import timedelta, timezone

    manila = timezone(timedelta(hours=8))
    # 2026-11-09 06:00 in Manila is still Sunday 22:00 UTC: the weekly period has not reset yet.
    moment = datetime(2026, 11, 9, 6, 0, tzinfo=manila)
    assert period_start_day("weekly", moment) == date(2026, 11, 2)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        period_start_day("daily", datetime(2026, 11, 4, 12, 0))


def test_unknown_period_is_rejected():
    with pytest.raises(ValueError):
        period_start_day("hourly", utc("2026-11-04 12:00"))
