"""Per-IP fixed-window rate limiting (in-process; see ADR-0002)."""

import math
import time
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal, Protocol

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
    """One ordered store per scope, each capped at `max_keys_per_scope` (ADR-0002 §1).

    Each store is in window-end order: a new or restarted window moves its key to the back, and
    a scope's window length is fixed for the life of the process (from `Settings`). Eviction pops
    from the front only, so it is O(1) amortised and never touches another scope's buckets. If a
    scope's window length ever varied, expired entries behind a live front would linger until cap
    pressure, which is still bounded.
    """

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_keys_per_scope: int = 10_000
    ) -> None:
        self._clock = clock
        self._max_keys = max_keys_per_scope
        # scope -> key -> (window end, count)
        self._scopes: dict[str, OrderedDict[str, tuple[float, int]]] = {}

    def hit(self, scope: str, key: str, limit: RateLimit) -> int | None:
        now = self._clock()
        store = self._scopes.setdefault(scope, OrderedDict())
        window = store.get(key)
        if window is None or window[0] <= now:
            store[key] = (now + limit.window_seconds, 1)
            store.move_to_end(key)
            self._evict(store, now)
            return None
        end, count = window
        if count >= limit.limit:
            return max(1, math.ceil(end - now))
        store[key] = (end, count + 1)
        return None

    def _evict(self, store: OrderedDict[str, tuple[float, int]], now: float) -> None:
        # The key just inserted is at the back and live, so it is never popped (cap >= 1).
        while store:
            end, _ = next(iter(store.values()))
            if end > now and len(store) <= self._max_keys:
                return
            store.popitem(last=False)


Scope = Literal["signup", "role_catalog", "login", "token", "jwks", "verify_email"]

_LIMITS: dict[str, Callable[[Settings], RateLimit]] = {
    "signup": lambda s: RateLimit(s.signup_rate_limit, s.signup_rate_window_seconds),
    "role_catalog": lambda s: RateLimit(
        s.role_catalog_rate_limit, s.role_catalog_rate_window_seconds
    ),
    "login": lambda s: RateLimit(s.login_rate_limit, s.login_rate_window_seconds),
    "token": lambda s: RateLimit(s.token_rate_limit, s.token_rate_window_seconds),
    "jwks": lambda s: RateLimit(s.jwks_rate_limit, s.jwks_rate_window_seconds),
    "verify_email": lambda s: RateLimit(
        s.verify_email_rate_limit, s.verify_email_rate_window_seconds
    ),
}


def rate_limit(scope: Scope) -> Any:
    """Route dependency: count the request per client IP for `scope`, 429 when over."""
    if scope not in _LIMITS:
        raise ValueError("unknown rate-limit scope")
    limit_for = _LIMITS[scope]

    async def _check(request: Request) -> None:
        settings: Settings = request.app.state.settings
        limiter: RateLimiter = request.app.state.rate_limiter
        key = ip_key(client_ip(request, settings.trusted_proxy_cidrs))
        retry_after = limiter.hit(scope, key, limit_for(settings))
        if retry_after is not None:
            raise RateLimitedError(retry_after)

    dep: Callable[[Request], Coroutine[Any, Any, None]] = _check
    return Depends(dep)
