import re

import pytest

from app.modules.rbac.origins import normalize_origin, normalize_origins

GENERIC = re.escape("origin must be https://host[:port], or http on localhost")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://App.Example.com", "https://app.example.com"),
        ("https://example.com/", "https://example.com"),
        ("https://example.com:443", "https://example.com"),
        ("https://example.com:8443", "https://example.com:8443"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("http://127.0.0.1:5173", "http://127.0.0.1:5173"),
        ("http://[::1]:8080", "http://[::1]:8080"),
        ("http://localhost:80", "http://localhost"),
        ("https://localhost:3000", "https://localhost:3000"),
        ("https://xn--bcher-kva.example", "https://xn--bcher-kva.example"),
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
    ],
)
def test_normalize_origin_rejects(value: str) -> None:
    with pytest.raises(ValueError, match=f"^{GENERIC}$"):
        normalize_origin(value)  # generic message: never echoes the value


def test_normalize_origins_dedupes_and_limits() -> None:
    assert normalize_origins(["https://A.io", "https://a.io/"]) == ["https://a.io"]
    assert normalize_origins([]) == []
    with pytest.raises(ValueError):
        normalize_origins([f"https://a{i}.io" for i in range(21)])
    with pytest.raises(ValueError):
        normalize_origins(["https://" + ".".join(["a" * 60] * 4) + ".abcd"])  # 256 chars
