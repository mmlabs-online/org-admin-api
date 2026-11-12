"""The `__Host-mm_device` cookie (R11, D20, ADR-0004): read from the raw header, set, clear.

The `__Host-` prefix (Secure, `Path=/`, no `Domain`) stops sibling `*.mmlabs.online` hosts from
setting or shadowing it (cookie tossing). A legacy `mm_device` is ignored.
"""

from fastapi import Request
from starlette.responses import Response

from app.core.config import Settings

DEVICE_COOKIE = "__Host-mm_device"
_PATH = "/"  # required by the `__Host-` prefix


def read_device_cookies(request: Request) -> list[str]:
    """Every `__Host-mm_device` value sent; Starlette's `request.cookies` keeps only one.

    The name match is exact and case-sensitive: `mm_device`, `__HOST-mm_device` and nameless
    parts are ignored.
    """
    values: list[str] = []
    for header in request.headers.getlist("cookie"):
        for part in header.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name == DEVICE_COOKIE:
                values.append(value)
    return values


def set_device_cookie(response: Response, value: str, settings: Settings) -> None:
    response.set_cookie(
        DEVICE_COOKIE,
        value,
        max_age=settings.device_session_idle_days * 86400,
        path=_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )  # no `domain`: host-only


def clear_device_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(DEVICE_COOKIE, path=_PATH, secure=True, httponly=True, samesite="lax")


def clear_device_cookie_header(settings: Settings) -> str:
    """The `Set-Cookie` value that clears the cookie, for error responses."""
    response = Response()
    clear_device_cookie(response, settings)
    return response.headers["set-cookie"]
