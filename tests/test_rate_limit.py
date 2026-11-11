from starlette.requests import Request

from app.core.client_ip import client_ip, ip_key
from app.core.rate_limit import InMemoryRateLimiter, RateLimit


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


LIMIT = RateLimit(limit=3, window_seconds=60)


def test_allows_up_to_limit_then_blocks() -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock())
    assert [limiter.hit("signup", "ip", LIMIT) for _ in range(3)] == [None, None, None]
    assert limiter.hit("signup", "ip", LIMIT) == 60


def test_window_resets() -> None:
    clock = FakeClock()
    limiter = InMemoryRateLimiter(clock=clock)
    for _ in range(4):
        limiter.hit("signup", "ip", LIMIT)
    clock.now += 60
    assert limiter.hit("signup", "ip", LIMIT) is None


def test_retry_after_is_seconds_left() -> None:
    clock = FakeClock()
    limiter = InMemoryRateLimiter(clock=clock)
    for _ in range(3):
        limiter.hit("signup", "ip", LIMIT)
    clock.now += 45.5
    assert limiter.hit("signup", "ip", LIMIT) == 15
    clock.now += 14.0
    assert limiter.hit("signup", "ip", LIMIT) == 1


def test_scopes_and_keys_are_independent() -> None:
    limiter = InMemoryRateLimiter(clock=FakeClock())
    for _ in range(3):
        limiter.hit("signup", "ip-a", LIMIT)
    assert limiter.hit("signup", "ip-a", LIMIT) is not None
    assert limiter.hit("signup", "ip-b", LIMIT) is None
    assert limiter.hit("login", "ip-a", LIMIT) is None


def test_evicts_expired_and_caps_keys() -> None:
    clock = FakeClock()
    limiter = InMemoryRateLimiter(clock=clock, max_keys=3)
    for key in ("a", "b", "c"):
        limiter.hit("s", key, LIMIT)
    clock.now += 61  # all expired
    limiter.hit("s", "d", LIMIT)
    assert set(limiter._windows) == {("s", "d")}
    for key in ("e", "f", "g"):
        clock.now += 1
        limiter.hit("s", key, LIMIT)
    assert len(limiter._windows) == 3
    assert ("s", "d") not in limiter._windows  # oldest dropped


def _request(peer: str, headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "client": (peer, 1234), "headers": raw})


def test_client_ip_ignores_headers_from_untrusted_peer() -> None:
    req = _request("203.0.113.5", {"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2"})
    assert client_ip(req, ["10.0.0.0/8"]) == "203.0.113.5"
    assert client_ip(req, []) == "203.0.113.5"


def test_client_ip_uses_cf_header_from_trusted_peer() -> None:
    req = _request("172.18.0.3", {"CF-Connecting-IP": "198.51.100.7", "X-Forwarded-For": "2.2.2.2"})
    assert client_ip(req, ["172.18.0.0/16"]) == "198.51.100.7"
    no_header = _request("172.18.0.3", {"X-Forwarded-For": "2.2.2.2"})
    assert client_ip(no_header, ["172.18.0.0/16"]) == "172.18.0.3"


def test_invalid_cf_header_falls_back_to_peer() -> None:
    req = _request("172.18.0.3", {"CF-Connecting-IP": "not-an-ip"})
    assert client_ip(req, ["172.18.0.0/16"]) == "172.18.0.3"


def test_ipv6_keyed_by_64() -> None:
    assert ip_key("2001:db8:1:2:aaaa::1") == "2001:db8:1:2::/64"
    assert ip_key("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    assert ip_key("2001:db8:1:3::1") != ip_key("2001:db8:1:2::1")
    assert ip_key("198.51.100.7") == "198.51.100.7"
