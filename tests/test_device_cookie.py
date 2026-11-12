"""`__Host-mm_device` (S3, D20): attributes, exact-name match, scope of use, OpenAPI."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.main import create_app
from app.modules.auth.cookies import clear_device_cookie_header
from app.modules.sessions.models import AccountSession, DeviceSession
from tests.auth_factories import (
    LOGIN,
    LOGOUT,
    auth_headers,
    login_body,
    make_device,
    make_login_app,
    make_login_user,
    parse_set_cookie,
    sha256,
    shift,
)


@pytest.fixture(autouse=True)
async def _app(db_session: AsyncSession) -> None:
    await make_login_app(db_session)


async def _device(db_session: AsyncSession, device_id: uuid.UUID) -> DeviceSession:
    device = await db_session.scalar(
        select(DeviceSession)
        .where(DeviceSession.id == device_id)
        .execution_options(populate_existing=True)
    )
    assert device is not None
    return device


def test_clear_header_matches_set_attributes() -> None:
    header = clear_device_cookie_header(Settings())
    assert header.startswith('__Host-mm_device="";')
    parts = {p.strip() for p in header.split(";")}
    assert {"Path=/", "Secure", "HttpOnly", "SameSite=lax", "Max-Age=0"} <= parts
    assert "domain" not in header.lower()


async def test_legacy_and_lookalike_names_ignored(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, device, (account,) = await make_device(db_session, user)
    lookalikes = "mm_device=junk; __HOST-mm_device=junk; =__Host-mm_device=junk"

    # Look-alikes next to the real cookie: ignored, not counted as duplicates.
    headers = auth_headers() | {"Cookie": f"{lookalikes}; __Host-mm_device={raw}"}
    resp = await client.post(LOGOUT, json={"account_id": str(account.id)}, headers=headers)
    assert resp.status_code == 204, resp.text

    # Only legacy / look-alike names: there is no device cookie.
    for cookie in (f"mm_device={raw}", f"__HOST-mm_device={raw}", f"=__Host-mm_device={raw}"):
        headers = auth_headers() | {"Cookie": cookie}
        resp = await client.post(LOGOUT, json={"all": True}, headers=headers)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "session_invalid"
        assert "set-cookie" not in resp.headers  # nothing was sent under the real name
    assert (await _device(db_session, device.id)).revoked_at is None

    # Login with only a legacy cookie mints a new device session.
    other = await make_login_user(db_session)
    headers = auth_headers() | {"Cookie": f"mm_device={raw}"}
    resp = await client.post(LOGIN, json=login_body(other), headers=headers)
    assert resp.status_code == 200, resp.text
    morsel = parse_set_cookie(resp)
    assert morsel is not None
    new = await db_session.scalar(
        select(DeviceSession).where(DeviceSession.token_hash == sha256(morsel.value))
    )
    assert new is not None and new.id != device.id


async def test_duplicate_host_cookie_still_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    headers = auth_headers() | {"Cookie": f"__Host-mm_device={raw}; __Host-mm_device=other"}
    resp = await client.post(LOGOUT, json={"all": True}, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"
    assert (await _device(db_session, device.id)).revoked_at is None
    active = await db_session.scalar(
        select(func.count())
        .select_from(AccountSession)
        .where(AccountSession.device_session_id == device.id, AccountSession.revoked_at.is_(None))
    )
    assert active == 1


async def test_non_auth_route_ignores_cookie(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    await shift(db_session, "device_sessions", "last_seen_at", device.id, "-2 hours")
    before = await _device(db_session, device.id)
    token_hash, last_seen_at = before.token_hash, before.last_seen_at

    for path in ("/healthz", "/api/v1/organizations"):
        resp = await client.get(path, headers=auth_headers(cookie=raw))
        assert resp.status_code == 200, resp.text
        assert "set-cookie" not in resp.headers

    after = await _device(db_session, device.id)
    assert after.token_hash == token_hash
    assert after.last_seen_at == last_seen_at


def test_openapi_device_cookie_scheme() -> None:
    schemes = create_app().openapi()["components"]["securitySchemes"]
    assert schemes["DeviceCookie"] == {"type": "apiKey", "in": "cookie", "name": "__Host-mm_device"}
