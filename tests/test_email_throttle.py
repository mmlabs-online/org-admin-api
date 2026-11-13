from datetime import UTC, datetime, timedelta

import pytest

from app.modules.auth.email_throttle import cap_refusal, resend_retry_after

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
        ("reset", 149, 0, 300, None),  # reset share: 50 % of 300 = 150
        ("reset", 150, 0, 300, "purpose_cap"),
        ("verify", 239, 239, 300, None),  # verify share: 80 % of 300 = 240
        ("verify", 240, 240, 300, "purpose_cap"),
        ("verify", 250, 100, 300, None),  # reset sends do not use the verify share
        ("verify", 299, 149, 300, None),  # reset at its ceiling cannot take verify's floor
        ("reset", 299, 240, 300, None),  # verify sends do not use the reset share
        ("reset", 300, 0, 300, "daily_cap"),  # the total cap binds every purpose
        ("verify", 300, 0, 300, "daily_cap"),
        ("reset", 0, 0, 1, None),  # cap 1: each share floors at 1
        ("verify", 0, 0, 1, None),
        ("verify", 1, 0, 1, "daily_cap"),
        ("reset", 1, 1, 1, "daily_cap"),
        ("reset", 2, 0, 5, "purpose_cap"),  # cap 5: reset share max(1, 2) = 2
        ("reset", 4, 3, 5, None),
        ("verify", 3, 3, 5, None),  # cap 5: verify share 4
        ("verify", 4, 4, 5, "purpose_cap"),
    ],
)
def test_cap_refusal(purpose: str, total: int, verify: int, cap: int, expected: str | None) -> None:
    assert cap_refusal(purpose, total_24h=total, verify_24h=verify, daily_cap=cap) == expected
