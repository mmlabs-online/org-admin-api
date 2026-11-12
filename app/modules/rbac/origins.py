"""Browser origins of an app, in one normal form (ADR-0003 §9; CORS echo in USR-03).

Only mmlabs origins are allowed (D19): `https://mmlabs.online` and `https://*.mmlabs.online` on
the default port (never the API's host or its subdomains), and loopback (`localhost`,
`127.0.0.1`, `[::1]`) on http or https, any port. Pure functions, no dependencies. Non-ASCII
hosts are rejected (no IDNA conversion): register the punycode form. Error messages never echo
the submitted value.
"""

import re
from collections.abc import Iterable

MAX_ORIGINS = 20
MAX_ORIGIN_LENGTH = 255

_ORIGIN_RE = re.compile(r"(https?)://(\[::1\]|[a-z0-9.-]+)(?::([0-9]{1,5}))?/?")
_LABEL_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
_DEFAULT_PORTS = {"https": "443", "http": "80"}
_MMLABS_HOST = "mmlabs.online"
_API_HOST = "api.mmlabs.online"
_MESSAGE = "origin must be https://mmlabs.online or one of its subdomains, or http(s) on localhost"


def normalize_origin(value: str) -> str:
    if len(value) > MAX_ORIGIN_LENGTH or not all("!" <= c <= "~" for c in value):
        raise ValueError(_MESSAGE)  # non-printable-ASCII, whitespace, too long
    match = _ORIGIN_RE.fullmatch(value.lower())
    if match is None:
        raise ValueError(_MESSAGE)
    scheme, host, port = match.groups()

    if host != "[::1]":
        labels = host.split(".")
        if len(host) > 253 or not all(_LABEL_RE.fullmatch(label) for label in labels):
            raise ValueError(_MESSAGE)
        if all(label.isdigit() for label in labels) and host != "127.0.0.1":
            raise ValueError(_MESSAGE)  # IP literals other than loopback
    mmlabs = host == _MMLABS_HOST or host.endswith("." + _MMLABS_HOST)
    if host not in _LOOPBACK_HOSTS and (
        scheme != "https"
        or not mmlabs
        or host == _API_HOST
        or host.endswith("." + _API_HOST)
        or port not in (None, "443")
    ):
        raise ValueError(_MESSAGE)  # D19: mmlabs hosts on https and the default port only

    if port is not None:
        if port.startswith("0") or not 1 <= int(port) <= 65535:
            raise ValueError(_MESSAGE)
        if port == _DEFAULT_PORTS[scheme]:
            port = None
    return f"{scheme}://{host}{':' + port if port else ''}"


def normalize_origins(values: list[str]) -> list[str]:
    if len(values) > MAX_ORIGINS:
        raise ValueError(f"at most {MAX_ORIGINS} origins")
    return list(dict.fromkeys(normalize_origin(v) for v in values))


def is_loopback_origin(origin: str) -> bool:
    """`origin` is already normalized (`scheme://host[:port]`)."""
    authority = origin.split("://", 1)[-1]
    host = "[::1]" if authority.startswith("[::1]") else authority.split(":")[0]
    return host in _LOOPBACK_HOSTS


def allowed_origins(origins: Iterable[str], app_env: str) -> list[str]:
    """An app's origins usable in `app_env`: loopback is ignored in staging/production."""
    if app_env in {"staging", "production"}:
        return [o for o in origins if not is_loopback_origin(o)]
    return list(origins)
