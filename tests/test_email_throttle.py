from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth.email_throttle import cap_allows, resend_retry_after

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def _ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


@pytest.mark.parametrize(
    ("last", "count", "oldest", "expected"),
    [
        (None, 0, None, None),  # nothing sent
        (_ago(10), 1, _ago(10), 50),  # 1 per minute
        (_ago(0), 1, _ago(0), 60),
        (_ago(59.5), 1, _ago(59.5), 1),  # rounded up, at least 1
        (_ago(60), 1, _ago(60), None),  # boundary: exactly one minute later
        (_ago(61), 4, _ago(3600), None),  # 4 in 24 h: allowed
        (_ago(61), 5, _ago(3600), 86400 - 3600),  # 5 in 24 h: until the oldest leaves
        (_ago(61), 5, _ago(86399.5), 1),
        (_ago(30), 5, _ago(3600), 30),  # the per-minute wait comes first
    ],
)
def test_resend_retry_after(
    last: datetime | None, count: int, oldest: datetime | None, expected: int | None
) -> None:
    assert resend_retry_after(NOW, last, count, oldest) == expected


@pytest.mark.parametrize(
    ("purpose", "total", "verify", "cap", "expected"),
    [
        ("verify", 239, 239, 300, True),  # verify share: 80 % of 300 = 240
        ("verify", 240, 240, 300, False),
        ("verify", 250, 100, 300, True),  # reset sends do not use the verify share
        ("reset", 299, 240, 300, True),  # reset may use the rest of the cap
        ("reset", 300, 240, 300, False),
        ("reset", 300, 0, 300, False),  # the total cap binds every purpose
        ("verify", 0, 0, 1, True),  # cap 1: the verify share floors at 1
        ("verify", 1, 1, 1, False),
        ("reset", 1, 1, 1, False),
        ("verify", 3, 3, 5, True),  # cap 5: verify share 4
        ("verify", 4, 4, 5, False),
        ("reset", 4, 4, 5, True),
    ],
)
def test_daily_cap_allows(purpose: str, total: int, verify: int, cap: int, expected: bool) -> None:
    assert cap_allows(purpose, total_24h=total, verify_24h=verify, daily_cap=cap) is expected
