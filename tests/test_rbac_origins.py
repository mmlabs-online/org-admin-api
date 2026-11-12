import re

import pytest

from app.modules.rbac.origins import (
    allowed_origins,
    is_loopback_origin,
    normalize_origin,
    normalize_origins,
)

GENERIC = re.escape(
    "origin must be https://mmlabs.online or one of its subdomains, or http(s) on localhost"
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://mmlabs.online", "https://mmlabs.online"),
        ("https://Stoktok.MMLabs.online/", "https://stoktok.mmlabs.online"),
        ("https://stoktok.mmlabs.online:443", "https://stoktok.mmlabs.online"),
        ("https://a.b.mmlabs.online", "https://a.b.mmlabs.online"),
        ("https://api2.mmlabs.online", "https://api2.mmlabs.online"),
        ("https://xn--abc.mmlabs.online", "https://xn--abc.mmlabs.online"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("http://127.0.0.1:5173", "http://127.0.0.1:5173"),
        ("http://[::1]:8080", "http://[::1]:8080"),
        ("http://localhost:80", "http://localhost"),
        ("https://localhost:3000", "https://localhost:3000"),
        ("https://127.0.0.1:8443", "https://127.0.0.1:8443"),
    ],
)
def test_normalize_origin_accepts(value: str, expected: str) -> None:
    assert normalize_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "ftp://x.io",
        "https://",
        "https://example.com.",
        "https://example.com/path",
        "https://example.com?q",
        "https://example.com#f",
        "https://user@example.com",
        "https://*.example.com",
        "null",
        "*",
        "https://bücher.example",
        "https://10.0.0.1",
        "https://[2001:db8::1]",
        "https://exa mple.com",
        " https://example.com",
        "https://example.com:0",
        "https://example.com:65536",
        "https://example.com:080",
        "https://ex%61mple.com",
        "https://example.com\\@evil.com",
        "https://-bad.example",
        "https://a..b",
        "https://example.com",
        "https://app.example.com:8443",
        "https://evilmmlabs.online",
        "https://mmlabs.online.attacker.com",
        "https://mmlabs.online.",
        "https://x.mmlabs.online:8443",
        "https://mmlabs.online:80",
        "http://mmlabs.online",
        "http://x.mmlabs.online",
        "https://api.mmlabs.online",
        "https://API.mmlabs.online:443",
        "https://x.api.mmlabs.online",
        "https://a.b.api.mmlabs.online",
        "https://0x7f.1",
        "https://a.123",
        "https://127.0.0.2",
        "https://xn--mmlabs-online.com",
    ],
)
def test_normalize_origin_rejects(value: str) -> None:
    with pytest.raises(ValueError, match=f"^{GENERIC}$"):
        normalize_origin(value)  # generic message: never echoes the value


def test_normalize_origins_dedupes_and_limits() -> None:
    assert normalize_origins(["https://A.mmlabs.online", "https://a.mmlabs.online/"]) == [
        "https://a.mmlabs.online"
    ]
    assert normalize_origins([]) == []
    with pytest.raises(ValueError):
        normalize_origins([f"https://a{i}.mmlabs.online" for i in range(21)])
    long_origin = "https://" + ".".join(["a" * 60] * 4) + ".mmlabs.online"
    assert len(long_origin) > 255
    with pytest.raises(ValueError):
        normalize_origins([long_origin])


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://localhost:3000", True),
        ("https://localhost", True),
        ("http://127.0.0.1:5173", True),
        ("http://[::1]:8080", True),
        ("http://[::1]", True),
        ("https://stoktok.mmlabs.online", False),
        ("https://localhost.mmlabs.online", False),
    ],
)
def test_is_loopback_origin(origin: str, expected: bool) -> None:
    assert is_loopback_origin(origin) is expected


@pytest.mark.parametrize(
    ("app_env", "expected"),
    [
        ("local", ["https://a.mmlabs.online", "http://localhost:3000", "http://[::1]:8080"]),
        ("test", ["https://a.mmlabs.online", "http://localhost:3000", "http://[::1]:8080"]),
        ("staging", ["https://a.mmlabs.online"]),
        ("production", ["https://a.mmlabs.online"]),
    ],
)
def test_allowed_origins_drops_loopback_in_staging_production(
    app_env: str, expected: list[str]
) -> None:
    origins = ["https://a.mmlabs.online", "http://localhost:3000", "http://[::1]:8080"]
    assert allowed_origins(origins, app_env) == expected
