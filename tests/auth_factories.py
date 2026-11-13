"""Helpers for login/logout and device-session tests (flush only; the test rolls back)."""

import hashlib
import uuid
from datetime import UTC, datetime
from http.cookies import Morsel, SimpleCookie
from typing import Any

from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.modules.auth.cookies import DEVICE_COOKIE
from app.modules.auth.passwords import PasswordHasher
from app.modules.rbac.models import App, Role
from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.users.models import User
from tests.conftest import TEST_IDENTITY_HMAC_KEY

PASSWORD = "correct horse battery staple"
ORIGIN = "https://stoktok.mmlabs.online"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
HMAC_KEY = TEST_IDENTITY_HMAC_KEY.encode()


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


async def make_login_user(
    session: AsyncSession,
    password: str = PASSWORD,
    *,
    email: str | None = None,
    status: str = "active",
    verified: bool = True,
) -> User:
    user = User(
        email=email or f"{uuid.uuid4().hex[:12]}@example.com",
        password_hash=await PasswordHasher(Settings()).hash(password),
        status=status,
        email_verified_at=datetime.now(UTC) if verified else None,
    )
    session.add(user)
    await session.flush()
    return user


async def make_login_app(
    session: AsyncSession,
    key: str = "stoktok",
    origins: list[str] | None = None,
    **kwargs: object,
) -> App:
    app = App(key=key, name=key.title(), origins=[ORIGIN] if origins is None else origins, **kwargs)
    session.add(app)
    await session.flush()
    return app


async def make_member_role(session: AsyncSession, app: App) -> Role:
    """Role `<key>:member`, set as the app's default role."""
    role = Role(app_id=app.id, key=f"{app.key}:member", name="Member", is_system=True)
    session.add(role)
    await session.flush()
    app.default_role_id = role.id
    await session.flush()
    return role


async def make_device(
    session: AsyncSession, *users: User, raw: str | None = None
) -> tuple[str, DeviceSession, list[AccountSession]]:
    """A device session whose current cookie is `raw`, with one account session per user."""
    raw = raw or uuid.uuid4().hex + uuid.uuid4().hex
    device = DeviceSession(token_hash=sha256(raw))
    session.add(device)
    await session.flush()
    accounts = [AccountSession(device_session_id=device.id, user_id=u.id) for u in users]
    session.add_all(accounts)
    await session.flush()
    return raw, device, accounts


async def shift(session: AsyncSession, table: str, column: str, row_id: object, delta: str) -> None:
    """Set `column = now() + interval delta` (e.g. "-25 hours") on one row."""
    assert table in {"device_sessions", "account_sessions"}
    sql = f"UPDATE {table} SET {column} = now() + CAST(CAST(:d AS text) AS interval)"  # noqa: S608
    await session.execute(text(sql + " WHERE id = :id"), {"d": delta, "id": row_id})


def auth_headers(
    app_key: str | None = "stoktok", origin: str | None = ORIGIN, cookie: str | None = None
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if app_key is not None:
        headers["X-MM-App"] = app_key
    if origin is not None:
        headers["Origin"] = origin
    if cookie is not None:
        headers["Cookie"] = f"{DEVICE_COOKIE}={cookie}"
    return headers


def parse_set_cookie(resp: Response) -> Morsel[str] | None:
    """The `__Host-mm_device` morsel of the response, or None when no cookie was set."""
    for header in resp.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        if DEVICE_COOKIE in jar:
            return jar[DEVICE_COOKIE]
    return None


def login_body(user: User, password: str = PASSWORD, **extra: object) -> dict[str, object]:
    return {"email": user.email, "password": password, **extra}


def recording_app(app: ASGIApp, events: list[str]) -> Any:
    """Wrap `app` so `events` gets "response.start" when `http.response.start` is sent."""

    async def wrapper(scope: Scope, receive: Receive, send: Send) -> None:
        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                events.append("response.start")
            await send(message)

        await app(scope, receive, _send)

    return wrapper
