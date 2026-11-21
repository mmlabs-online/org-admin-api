"""ADM-039: calendar periods in Asia/Ho_Chi_Minh (OQ63)."""

from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from app.modules.usage.models import UsagePeriod
from app.modules.usage.periods import period_bounds, period_end, period_start

ICT = timezone(timedelta(hours=7))
W, D, M = UsagePeriod.WEEK, UsagePeriod.DAY, UsagePeriod.MONTH


def test_week_sunday_235959_ict_belongs_to_previous_monday() -> None:
    at = datetime(2026, 11, 22, 23, 59, 59, 999999, tzinfo=ICT)
    assert period_start(W, at) == datetime(2026, 11, 15, 17, 0, tzinfo=UTC)
    assert period_end(W, at) == datetime(2026, 11, 22, 17, 0, tzinfo=UTC)


def test_week_monday_0000_ict_starts_new_week() -> None:
    at = datetime(2026, 11, 23, 0, 0, tzinfo=ICT)
    assert period_start(W, at) == at
    assert period_end(W, at) == datetime(2026, 11, 29, 17, 0, tzinfo=UTC)


def test_day_utc_1659_vs_1700_crossing() -> None:
    before = datetime(2026, 11, 20, 16, 59, 59, tzinfo=UTC)
    after = datetime(2026, 11, 20, 17, 0, 0, tzinfo=UTC)
    assert period_start(D, before) == datetime(2026, 11, 19, 17, 0, tzinfo=UTC)
    assert period_start(D, after) == datetime(2026, 11, 20, 17, 0, tzinfo=UTC)
    assert period_end(D, before) == after


def test_month_last_second_and_first_instant() -> None:
    last = datetime(2026, 11, 30, 23, 59, 59, tzinfo=ICT)
    first = datetime(2026, 12, 1, 0, 0, tzinfo=ICT)
    assert period_start(M, last) == datetime(2026, 11, 1, tzinfo=ICT)
    assert period_end(M, last) == first
    assert period_start(M, first) == first
    apr = datetime(2026, 4, 30, 23, 59, 59, tzinfo=ICT)
    assert period_end(M, apr) == datetime(2026, 5, 1, tzinfo=ICT)


def test_month_end_year_rollover() -> None:
    at = datetime(2026, 12, 31, 23, 59, 59, tzinfo=ICT)
    assert period_bounds(M, at) == (
        datetime(2026, 12, 1, tzinfo=ICT),
        datetime(2027, 1, 1, tzinfo=ICT),
    )


def test_february_non_leap_and_leap() -> None:
    assert period_end(M, datetime(2027, 2, 28, 12, tzinfo=ICT)) == datetime(2027, 3, 1, tzinfo=ICT)
    assert period_bounds(M, datetime(2028, 2, 29, 12, tzinfo=ICT)) == (
        datetime(2028, 2, 1, tzinfo=ICT),
        datetime(2028, 3, 1, tzinfo=ICT),
    )


def test_week_spanning_month_and_year() -> None:
    assert period_start(W, datetime(2026, 1, 1, 12, tzinfo=ICT)) == datetime(
        2025, 12, 29, tzinfo=ICT
    )


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        period_start(D, datetime(2026, 11, 20, 12, 0))


def test_other_offset_same_result() -> None:
    a = datetime(2026, 11, 20, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    b = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
    assert a == b
    assert period_bounds(D, a) == period_bounds(D, b)


def test_results_are_aware_utc() -> None:
    start, end = period_bounds(W, datetime(2026, 11, 20, tzinfo=UTC))
    assert start.tzinfo is UTC
    assert end.tzinfo is UTC


SAMPLES = [
    datetime(2026, 11, 22, 16, 59, 59, tzinfo=UTC),
    datetime(2026, 11, 22, 17, 0, 0, tzinfo=UTC),
    datetime(2026, 12, 31, 16, 59, 59, tzinfo=UTC),
    datetime(2026, 12, 31, 17, 0, 0, tzinfo=UTC),
    datetime(2028, 2, 29, 5, 0, 0, tzinfo=UTC),
    datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
]


@pytest.mark.parametrize("period", list(UsagePeriod))
@pytest.mark.parametrize("at", SAMPLES)
def test_bounds_contain_instant_and_chain(period: UsagePeriod, at: datetime) -> None:
    start, end = period_bounds(period, at)
    assert start <= at < end
    assert period_start(period, end) == end
    assert period_start(period, end - timedelta(microseconds=1)) == start


@pytest.mark.parametrize("period", ["day", "week", "month"])
def test_plain_string_period_matches_enum(period: str) -> None:
    """A period read back from the DB is a plain `str`, not the enum (ADM-039 review nit)."""
    at = datetime(2026, 11, 18, 5, 0, tzinfo=UTC)
    plain = cast(UsagePeriod, period)
    enum = UsagePeriod(period)
    assert period_start(plain, at) == period_start(enum, at)
    assert period_end(plain, at) == period_end(enum, at)
