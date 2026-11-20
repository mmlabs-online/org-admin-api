"""Calendar usage periods in Asia/Ho_Chi_Minh (OQ63, ADR-0024). Pure: no database.

ICT has no DST, so wall-clock arithmetic is exact. Periods are `[start, end)`; weeks start on
Monday 00:00, months on the 1st 00:00. Results are aware UTC datetimes, ready to be bound as SQL
parameters.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.usage.models import UsagePeriod

USAGE_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _local_start(period: UsagePeriod, at: datetime) -> datetime:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("`at` must be timezone-aware")
    local = at.astimezone(USAGE_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if period is UsagePeriod.WEEK:
        return local - timedelta(days=local.weekday())
    if period is UsagePeriod.MONTH:
        return local.replace(day=1)
    return local


def _next_local(period: UsagePeriod, start: datetime) -> datetime:
    if period is UsagePeriod.DAY:
        return start + timedelta(days=1)
    if period is UsagePeriod.WEEK:
        return start + timedelta(days=7)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def period_start(period: UsagePeriod, at: datetime) -> datetime:
    """Start (inclusive, UTC) of the calendar period of kind `period` containing `at`."""
    return _local_start(period, at).astimezone(UTC)


def period_end(period: UsagePeriod, at: datetime) -> datetime:
    """Start (exclusive end, UTC) of the next period."""
    return _next_local(period, _local_start(period, at)).astimezone(UTC)


def period_bounds(period: UsagePeriod, at: datetime) -> tuple[datetime, datetime]:
    start = _local_start(period, at)
    return start.astimezone(UTC), _next_local(period, start).astimezone(UTC)
