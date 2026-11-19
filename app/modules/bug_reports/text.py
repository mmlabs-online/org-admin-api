"""Pure text rules for bug-report fields (ADM-028). No I/O, so they are unit-testable alone.

The limits match the CHECK constraints in the `bug_reports` migration."""

import re
from urllib.parse import urlsplit

DESCRIPTION_MAX = 5000
PAGE_URL_MAX = 2048
APP_VERSION_MAX = 64
USER_AGENT_MAX = 512

# C0/C1 controls and lone surrogates (NUL would make Postgres answer 500).
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
_DESCRIPTION_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]")
_APP_VERSION = re.compile(rf"^[\x20-\x7e]{{1,{APP_VERSION_MAX}}}$")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def clean_description(value: str) -> str:
    """Strip outer whitespace; 1..5000 characters; no control characters except \\n \\r \\t."""
    value = value.strip()
    if not 1 <= len(value) <= DESCRIPTION_MAX:
        raise ValueError("description length")
    if _DESCRIPTION_CONTROL.search(value):
        raise ValueError("control characters")
    return value


def check_printable(value: str, max_length: int) -> str:
    """1..`max_length` characters without control characters (user agent)."""
    if not 1 <= len(value) <= max_length or _CONTROL.search(value):
        raise ValueError("invalid text")
    return value


def check_app_version(value: str) -> str:
    if _APP_VERSION.fullmatch(value) is None:
        raise ValueError("invalid app version")
    return value


def reduce_page_url(value: str) -> str:
    """`scheme://host[:port]/path` only: userinfo, query and fragment are dropped, the host is
    lowercased, a default port is dropped and an empty path becomes `/`. Only http(s)."""
    if len(value) > PAGE_URL_MAX or _CONTROL.search(value) or any(c.isspace() for c in value):
        raise ValueError("invalid url")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid url") from exc
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not host:
        raise ValueError("invalid url")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port is None or port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
    return f"{scheme}://{netloc}{parts.path or '/'}"
