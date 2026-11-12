"""The `mm_device` cookie (R11, ADR-0004): read from the raw header, set, clear."""

from fastapi import Request
from starlette.responses import Response

from app.core.config import Settings

DEVICE_COOKIE = "mm_device"


def _path(settings: Settings) -> str:
    return f"{settings.api_prefix}/auth"


def read_device_cookies(request: Request) -> list[str]:
    """Every `mm_device` value sent; Starlette's `request.cookies` would keep only one."""
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
        path=_path(settings),
        secure=True,
        httponly=True,
        samesite="lax",
    )  # no `domain`: host-only


def clear_device_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        DEVICE_COOKIE, path=_path(settings), secure=True, httponly=True, samesite="lax"
    )


def clear_device_cookie_header(settings: Settings) -> str:
    """The `Set-Cookie` value that clears the cookie, for error responses."""
    response = Response()
    clear_device_cookie(response, settings)
    return response.headers["set-cookie"]
