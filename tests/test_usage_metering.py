"""ADM-042 pure metering rules (no database)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.usage import metering
from app.modules.usage.errors import IdempotencyKeyReusedError
from app.modules.usage.models import UsagePeriod

U, M = uuid.uuid4(), uuid.uuid4()


def test_remaining_clamps_at_zero() -> None:
    assert metering.remaining(5, 2) == 3
    assert metering.remaining(5, 5) == 0
    assert metering.remaining(5, 9) == 0


def test_charged_per_state() -> None:
    assert metering.charged("consumed", 4, None) == 4
    assert metering.charged("settled", 4, 7) == 7
    assert metering.charged("refunded", 4, 7) == 0
    assert metering.charged("refunded", 4, None) == 0


def test_replay_or_conflict_same() -> None:
    metering.replay_or_conflict(stored=(U, M, 3), user_id=U, metric_id=M, amount=3)


@pytest.mark.parametrize(
    ("user", "metric", "amount"),
    [(uuid.uuid4(), M, 3), (U, uuid.uuid4(), 3), (U, M, 4)],
    ids=["user", "metric", "amount"],
)
def test_replay_or_conflict_differs(user: uuid.UUID, metric: uuid.UUID, amount: int) -> None:
    with pytest.raises(IdempotencyKeyReusedError):
        metering.replay_or_conflict(stored=(U, M, 3), user_id=user, metric_id=metric, amount=amount)


def test_settle_delta_up_down() -> None:
    assert metering.settle_delta(5, 8) == 3
    assert metering.settle_delta(5, 2) == -3
    assert metering.settle_delta(5, 5) == 0


def test_window_open_boundaries() -> None:
    t = datetime(2026, 11, 22, tzinfo=UTC)
    day = 86400
    assert metering.window_open(t, t + timedelta(seconds=day - 1), day)
    assert not metering.window_open(t, t + timedelta(seconds=day), day)  # exactly 24 h: closed
    assert not metering.window_open(t, t + timedelta(seconds=day + 1), day)


def test_counter_period_uses_metric_kind_and_db_now() -> None:
    # Sunday 2026-11-01 20:00 UTC = Monday 2026-11-02 03:00 ICT: the week and the month differ.
    now = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    week, w_start, w_end = metering.counter_period("week", now)
    month, m_start, _ = metering.counter_period("month", now)
    assert (week, month) == (UsagePeriod.WEEK, UsagePeriod.MONTH)
    assert w_start == datetime(2026, 11, 1, 17, 0, tzinfo=UTC)  # Mon 00:00 ICT
    assert w_end - w_start == timedelta(days=7)
    assert m_start == datetime(2026, 10, 31, 17, 0, tzinfo=UTC)  # 1 Nov 00:00 ICT
    assert (week, w_start) != (month, m_start)
