"""Per-email send limits for verification/reset links (D3, §5 S6): 1 per minute, 5 per 24 h."""

import math
from datetime import datetime, timedelta

MIN_INTERVAL = timedelta(seconds=60)
DAILY_WINDOW = timedelta(hours=24)
DAILY_LIMIT = 5
DAILY_CAP_RETRY_SECONDS = 3600  # global cap reached: retry later


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
