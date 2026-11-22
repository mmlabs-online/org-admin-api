"""Pure metering rules (ADM-042, ADR-0022 §5). No database, no clock: callers pass `now`."""

import uuid
from datetime import UTC, datetime, timedelta

from app.modules.usage.errors import IdempotencyKeyReusedError
from app.modules.usage.models import UsageEventState, UsagePeriod
from app.modules.usage.periods import period_bounds


def remaining(limit: int, used: int) -> int:
    """Never negative: a settle may push `used` past the limit."""
    return max(0, limit - used)


def charged(state: str, amount: int, settled_amount: int | None) -> int:
    """The event's current net charge."""
    if state == UsageEventState.REFUNDED.value:
        return 0
    if state == UsageEventState.SETTLED.value and settled_amount is not None:
        return settled_amount
    return amount


def replay_or_conflict(
    *,
    stored: tuple[uuid.UUID, uuid.UUID, int],
    user_id: uuid.UUID,
    metric_id: uuid.UUID,
    amount: int,
) -> None:
    """A retried consume must carry the stored fingerprint (user, metric, amount)."""
    if stored != (user_id, metric_id, amount):
        raise IdempotencyKeyReusedError("The idempotency key was used with a different request")


def settle_delta(consumed: int, actual: int) -> int:
    """Counter change of a settle: positive charges more, negative credits."""
    return actual - consumed


def window_open(created_at: datetime, now: datetime, window_seconds: int) -> bool:
    """Settle and refund are allowed strictly inside the window (exactly the window = closed)."""
    return now - created_at < timedelta(seconds=window_seconds)


def counter_period(metric_period: str, now: datetime) -> tuple[UsagePeriod, datetime, datetime]:
    """The metric's current period kind with its [start, end) at the database's `now`."""
    kind = UsagePeriod(metric_period)
    start, end = period_bounds(kind, now.astimezone(UTC))
    return kind, start, end
