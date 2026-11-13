"""Per-email send limits for verification/reset links (D3, §5 S6): 1 per minute, 5 per 24 h;
and the per-purpose split of the global daily cap (USR-04b, USR-04d)."""

import math
from datetime import datetime, timedelta

MIN_INTERVAL = timedelta(seconds=60)
DAILY_WINDOW = timedelta(hours=24)
DAILY_LIMIT = 5
DAILY_CAP_RETRY_SECONDS = 3600  # global cap reached: retry later
VERIFY_CAP_PERCENT = 80  # verification may use at most this share of the daily cap
RESET_CAP_PERCENT = 50  # password reset may use at most this share of the daily cap


def resend_retry_after(
    now: datetime, last: datetime | None, count_24h: int, oldest_24h: datetime | None
) -> int | None:
    """None when a send is allowed, else seconds until it is (at least 1).

    `last`, `count_24h` and `oldest_24h` describe the sends of the last 24 h.
    """
    if last is None:
        return None
    wait = (last + MIN_INTERVAL - now).total_seconds()
    if wait > 0:
        return max(1, math.ceil(wait))
    if count_24h >= DAILY_LIMIT and oldest_24h is not None:
        return max(1, math.ceil((oldest_24h + DAILY_WINDOW - now).total_seconds()))
    return None


def cap_refusal(purpose: str, *, total_24h: int, verify_24h: int, daily_cap: int) -> str | None:
    """None when one more send of `purpose` fits the global daily cap, else the reason:
    `"daily_cap"` (the total cap) or `"purpose_cap"` (the purpose's share).

    Each purpose may use at most its share of the cap (at least 1): verify
    `VERIFY_CAP_PERCENT`, reset `RESET_CAP_PERCENT`. So neither can starve the other: verify
    always keeps at least 50 % of the cap, reset at least 20 %. Constants, not settings: they
    are safety invariants of the split (ADR-0007, USR-04d).
    """
    if total_24h >= daily_cap:
        return "daily_cap"
    if purpose == "verify":
        used, percent = verify_24h, VERIFY_CAP_PERCENT
    else:
        used, percent = total_24h - verify_24h, RESET_CAP_PERCENT
    if used >= max(1, daily_cap * percent // 100):
        return "purpose_cap"
    return None
