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
    ("purpose", "total", "verify", "invite", "cap", "expected"),
    [
        ("reset", 119, 0, 0, 300, None),  # reset share: 40 % of 300 = 120
        ("reset", 120, 0, 0, 300, "purpose_cap"),
        ("verify", 209, 209, 0, 300, None),  # verify share: 70 % of 300 = 210
        ("verify", 210, 210, 0, 300, "purpose_cap"),
        ("invite", 29, 0, 29, 300, None),  # invite share: 10 % of 300 = 30
        ("invite", 30, 0, 30, 300, "purpose_cap"),
        ("verify", 250, 100, 0, 300, None),  # reset sends do not use the verify share
        ("reset", 299, 210, 0, 300, None),  # verify sends do not use the reset share
        ("reset", 150, 0, 40, 300, None),  # invite sends do not use the reset share
        ("verify", 150, 100, 50, 300, None),  # invite sends do not use the verify share
        ("invite", 250, 100, 0, 300, None),  # verify/reset sends do not use the invite share
        ("reset", 300, 0, 0, 300, "daily_cap"),  # the total cap binds every purpose
        ("verify", 300, 0, 0, 300, "daily_cap"),
        ("invite", 300, 0, 0, 300, "daily_cap"),
        ("reset", 0, 0, 0, 1, None),  # cap 1: each share floors at 1
        ("verify", 0, 0, 0, 1, None),
        ("invite", 0, 0, 0, 1, None),
        ("verify", 1, 0, 0, 1, "daily_cap"),
        ("reset", 1, 1, 0, 1, "daily_cap"),
        ("reset", 2, 0, 0, 5, "purpose_cap"),  # cap 5: reset share max(1, 2) = 2
        ("reset", 4, 3, 0, 5, None),
        ("verify", 2, 2, 0, 5, None),  # cap 5: verify share 3
        ("verify", 3, 3, 0, 5, "purpose_cap"),
        ("invite", 1, 0, 1, 5, "purpose_cap"),  # cap 5: invite share max(1, 0) = 1
    ],
)
def test_cap_refusal(
    purpose: str, total: int, verify: int, invite: int, cap: int, expected: str | None
) -> None:
    assert (
        cap_refusal(purpose, total_24h=total, verify_24h=verify, invite_24h=invite, daily_cap=cap)
        == expected
    )


def test_invite_share_cannot_take_reset_floor() -> None:
    # Verify and invite at their ceilings (210 + 30 of 300): reset still gets its 20 % floor (60).
    for sent in range(60):
        refusal = cap_refusal(
            "reset", total_24h=240 + sent, verify_24h=210, invite_24h=30, daily_cap=300
        )
        assert refusal is None
    assert (
        cap_refusal("reset", total_24h=300, verify_24h=210, invite_24h=30, daily_cap=300)
        == "daily_cap"
    )


def test_resend_retry_after_custom_daily_limit() -> None:
    assert resend_retry_after(NOW, _ago(61), 2, _ago(3600), daily_limit=3) is None
    assert resend_retry_after(NOW, _ago(61), 3, _ago(3600), daily_limit=3) == 86400 - 3600
    assert resend_retry_after(NOW, _ago(61), 3, _ago(3600)) is None  # the default limit is 5
