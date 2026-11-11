"""Per-IP fixed-window rate limiting (in-process; see ADR-0002)."""

import math
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Depends, Request

from app.core.client_ip import client_ip, ip_key
from app.core.config import Settings
from app.core.errors import RateLimitedError


@dataclass(frozen=True)
class RateLimit:
    limit: int
    window_seconds: int


class RateLimiter(Protocol):
    def hit(self, scope: str, key: str, limit: RateLimit) -> int | None:
        """Count one attempt. None = allowed, else seconds until the window resets."""
        ...


class InMemoryRateLimiter:
    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_keys: int = 10_000
    ) -> None:
        self._clock = clock
        self._max_keys = max_keys
        # (scope, key) -> (window end, count)
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}

    def hit(self, scope: str, key: str, limit: RateLimit) -> int | None:
        now = self._clock()
        bucket = (scope, key)
        window = self._windows.get(bucket)
        if window is None or window[0] <= now:
            self._windows[bucket] = (now + limit.window_seconds, 1)
            self._evict(now)
            return None
        end, count = window
        if count >= limit.limit:
            return max(1, math.ceil(end - now))
        self._windows[bucket] = (end, count + 1)
        return None

    def _evict(self, now: float) -> None:
        if len(self._windows) <= self._max_keys:
            return
        for bucket in [b for b, (end, _) in self._windows.items() if end <= now]:
            del self._windows[bucket]
        if len(self._windows) > self._max_keys:
            oldest = sorted(self._windows, key=lambda b: self._windows[b][0])
            for bucket in oldest[: len(self._windows) - self._max_keys]:
                del self._windows[bucket]


def _limit_for(scope: str, settings: Settings) -> RateLimit:
    limits = {
        "signup": RateLimit(settings.signup_rate_limit, settings.signup_rate_window_seconds),
    }
    return limits[scope]


def rate_limit(scope: str) -> Any:
    """Route dependency: count the request per client IP for `scope`, 429 when over."""

    async def _check(request: Request) -> None:
        settings: Settings = request.app.state.settings
        limiter: RateLimiter = request.app.state.rate_limiter
        key = ip_key(client_ip(request, settings.trusted_proxy_cidrs))
        retry_after = limiter.hit(scope, key, _limit_for(scope, settings))
        if retry_after is not None:
            raise RateLimitedError(retry_after)

    dep: Callable[[Request], Coroutine[Any, Any, None]] = _check
    return Depends(dep)
