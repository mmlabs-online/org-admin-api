"""Per-email login backoff and Turnstile thresholds (D11, ADR-0002 §5)."""

import hashlib
import hmac
from datetime import timedelta

TURNSTILE_AFTER = 3  # failures before Turnstile is required
BACKOFF_AFTER = 5  # failures before the email is blocked
BACKOFF_CAP_MIN = 15
COUNTER_RESET = timedelta(hours=24)  # without failures, the counter starts again


def backoff_minutes(failures: int) -> int | None:
    """Block after the n-th failure: 5 -> 1 min, 6 -> 2, 7 -> 4, 8 -> 8, then 15 (cap)."""
    if failures < BACKOFF_AFTER:
        return None
    minutes: int = 2 ** (failures - BACKOFF_AFTER)
    return min(minutes, BACKOFF_CAP_MIN)


class EmailHasher:
    """HMAC-SHA256 of a normalized email, so counters never store the email (R11)."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def hmac(self, email: str) -> bytes:
        return hmac.new(self._key, email.encode("utf-8"), hashlib.sha256).digest()
