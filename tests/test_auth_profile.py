"""POST /api/v1/auth/profile (USR-33, D40): self-service display name, cookie class."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.rbac.models import App, Role
from app.modules.sessions.models import AccountSession
from app.modules.users import profile_service
from app.modules.users import service as staff_service
from app.modules.users.models import User
from tests.app_credential_factories import make_credential
from tests.auth_factories import (
    ORIGIN,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
)
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_app, make_org, make_role

URL = "/api/v1/auth/profile"
TOKEN = "/api/v1/auth/token"
ACTION = "user.display_name_changed"


def _error(resp: Any) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


def _headers(raw: str, app_key: str | None = "stoktok", origin: str | None = ORIGIN) -> Any:
    return auth_headers(app_key=app_key, origin=origin, cookie=raw)


def _body(account: AccountSession, name: Any = "Ming") -> dict[str, Any]:
    return {"account_id": str(account.id), "display_name": name}


async def _env(db: AsyncSession) -> tuple[App, Role]:
    app = await make_login_app(db, "stoktok")
    role = await make_role(db, app, "stoktok:member")
    app.default_role_id = role.id
    await db.flush()
    return app, role


async def _member(
    db: AsyncSession, role: Role | None = None, **kw: Any
) -> tuple[User, str, AccountSession]:
    user = await make_login_user(db, **kw)
    if role is not None:
        await assign(db, user, role)
    raw, _, (account,) = await make_device(db, user)
    return user, raw, account


async def _audit(db: AsyncSession, *users: User) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.action == ACTION, AuditLog.target_id.in_([u.id for u in users]))
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _name(db: AsyncSession, user: User) -> str | None:
    await db.refresh(user)
    return user.display_name


# --- happy paths --------------------------------------------------------------------


async def test_set_name_returns_account_read(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "account_id": str(account.id),
        "email": user.email,
        "email_verified": True,
        "display_name": "Ming",
    }
    assert resp.headers["cache-control"] == "no-store"
    listed = await client.get("/api/v1/auth/accounts", headers=_headers(raw))
    assert listed.json()["items"][0]["display_name"] == "Ming"


async def test_null_clears_name(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    user.display_name = "Old"
    await db_session.flush()
    resp = await client.post(URL, json=_body(account, None), headers=_headers(raw))
    assert resp.status_code == 200
    assert resp.json()["display_name"] is None
    assert await _name(db_session, user) is None


async def test_same_value_is_noop(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    first = await client.post(URL, json=_body(account), headers=_headers(raw))
    await db_session.refresh(user)
    stamp = user.updated_at
    second = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert (first.status_code, second.status_code) == (200, 200)
    await db_session.refresh(user)
    assert user.updated_at == stamp
    assert len(await _audit(db_session, user)) == 1


async def test_set_name_bumps_updated_at(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    await db_session.refresh(user)
    before = user.updated_at
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 200
    await db_session.refresh(user)
    assert user.updated_at > before


async def test_name_stored_normalised(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(account, "  Ming   Le "), headers=_headers(raw))
    assert resp.status_code == 200
    assert await _name(db_session, user) == "Ming Le"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "a" * 101,
        "zzbad‮",
        "zzbad​",
        "zzbad\x07",
        "zzbad\tx",
        "zzbadㅤ",
        "zzbad⠀",
        "zzbad̀́̂",
        123,
    ],
)
async def test_invalid_name_422_no_echo(
    client: AsyncClient, db_session: AsyncSession, name: Any
) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(account, name), headers=_headers(raw))
    assert resp.status_code == 422
    assert _error(resp)["code"] == "validation_error"
    assert "zzbad" not in resp.text
    assert await _name(db_session, user) is None
    assert await _audit(db_session, user) == []


async def test_missing_field_and_extra_field_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _env(db_session)
    _, raw, account = await _member(db_session, role)
    missing = await client.post(URL, json={"account_id": str(account.id)}, headers=_headers(raw))
    extra = await client.post(
        URL, json={**_body(account), "email": "x@y.io"}, headers=_headers(raw)
    )
    assert (missing.status_code, extra.status_code) == (422, 422)


async def test_multi_account_device_targets_body_account(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _env(db_session)
    a = await make_login_user(db_session)
    b = await make_login_user(db_session)
    raw, _, (_, acc_b) = await make_device(db_session, a, b)
    resp = await client.post(URL, json=_body(acc_b, "Bee"), headers=_headers(raw))
    assert resp.status_code == 200
    assert await _name(db_session, b) == "Bee"
    assert await _name(db_session, a) is None


# --- auth ---------------------------------------------------------------------------


async def test_no_cookie_401(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    _, _, account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(account), headers=auth_headers())
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"


async def test_logged_out_account_401(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    account.revoked_at = datetime.now(UTC)
    await db_session.flush()
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert await _name(db_session, user) is None


async def test_disabled_user_401(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role, status="disabled")
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert await _name(db_session, user) is None


async def test_other_device_account_id_401_swap(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _env(db_session)
    mine, raw, _acc = await _member(db_session, role)
    other, _, other_account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(other_account, "Evil"), headers=_headers(raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert await _name(db_session, mine) is None
    assert await _name(db_session, other) is None
    assert await _audit(db_session, mine, other) == []


async def test_missing_app_header_403(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(account), headers=_headers(raw, app_key=None))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "app_header_required"
    assert await _name(db_session, user) is None


async def test_wrong_origin_403(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    resp = await client.post(
        URL, json=_body(account), headers=_headers(raw, origin="https://evil.example")
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "origin_not_allowed"
    assert await _name(db_session, user) is None


async def test_unverified_email_403(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role, verified=False)
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "email_unverified"
    assert await _name(db_session, user) is None


async def test_admin_app_header_allowed(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.origins = [ORIGIN]
    await db_session.flush()
    user, raw, account = await _member(db_session)
    resp = await client.post(URL, json=_body(account), headers=_headers(raw, app_key="admin"))
    assert resp.status_code == 200, resp.text
    assert await _name(db_session, user) == "Ming"


async def test_ip_rate_limit_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    _, raw, account = await _member(db_session, role)
    async with make_client(profile_rate_limit=1) as client:
        first = await client.post(URL, json=_body(account), headers=_headers(raw))
        second = await client.post(URL, json=_body(account, "Other"), headers=_headers(raw))
    assert first.status_code == 200
    assert second.status_code == 429
    assert _error(second)["code"] == "rate_limited"
    assert "retry-after" in second.headers


async def test_per_user_rate_limit_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    async with make_client(profile_user_rate_limit=1) as client:
        first = await client.post(URL, json=_body(account), headers=_headers(raw))
        second = await client.post(URL, json=_body(account, "Other"), headers=_headers(raw))
    assert first.status_code == 200
    assert second.status_code == 429
    assert _error(second)["code"] == "rate_limited"
    assert "retry-after" in second.headers
    assert await _name(db_session, user) == "Ming"


# --- effects on tokens and the member read ------------------------------------------


async def test_next_token_carries_new_name(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    _, raw, account = await _member(db_session, role)
    token_body = {"audience": "stoktok", "account_id": str(account.id)}

    async def claims() -> dict[str, Any]:
        resp = await client.post(TOKEN, json=token_body, headers=_headers(raw))
        assert resp.status_code == 200, resp.text
        decoded: dict[str, Any] = jwt.decode(
            resp.json()["access_token"], options={"verify_signature": False}
        )
        return decoded

    await client.post(URL, json=_body(account, "Nguyễn Văn A"), headers=_headers(raw))
    assert (await claims())["name"] == "Nguyễn Văn A"
    await client.post(URL, json=_body(account, None), headers=_headers(raw))
    assert "name" not in await claims()


async def test_member_read_shows_new_name(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    reader = await make_app(db_session, "readerapp", org_mode="required")
    org = await make_org(db_session)
    await assign(
        db_session, user, await make_role(db_session, reader, "readerapp:member"), org_id=org.id
    )
    _, key = await make_credential(db_session, reader, scope="org-members:read")
    before = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    resp = await client.post(URL, json=_body(account, "Seen At Once"), headers=_headers(raw))
    assert resp.status_code == 200
    since = before.isoformat().replace("+", "%2B")
    read = await client.get(
        f"/api/v1/apps/readerapp/orgs/{org.id}/members?updated_since={since}",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert read.status_code == 200, read.text
    items = read.json()["items"]
    assert [(i["user_id"], i["display_name"]) for i in items] == [(str(user.id), "Seen At Once")]


# --- audit and logs -----------------------------------------------------------------


async def test_audit_row_has_no_value(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    await client.post(URL, json=_body(account, "zzauditmarker"), headers=_headers(raw))
    await client.post(URL, json=_body(account, None), headers=_headers(raw))
    clear_row, set_row = sorted(  # same-transaction rows share `occurred_at`
        await _audit(db_session, user), key=lambda r: r.changes["display_name"]["set"]
    )
    assert (set_row.actor_kind, set_row.actor_user_id) == ("user", user.id)
    assert (set_row.target_type, set_row.target_id) == ("user", user.id)
    assert set_row.app_key == "stoktok"
    assert set_row.changes == {"display_name": {"set": True}}
    assert clear_row.changes == {"display_name": {"set": False}}
    assert "zzauditmarker" not in str(set_row.changes) + str(clear_row.changes)


async def test_logs_never_contain_name(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Alembic's `fileConfig` (test DB setup) disables existing loggers.
    monkeypatch.setattr(profile_service.logger, "disabled", False)
    monkeypatch.setattr(staff_service.logger, "disabled", False)
    _, role = await _env(db_session)
    user, raw, account = await _member(db_session, role)
    with caplog.at_level(logging.DEBUG, logger="app"):
        ok = await client.post(URL, json=_body(account, "zznamemarker"), headers=_headers(raw))
        bad = await client.post(URL, json=_body(account, "zznamemarker‮"), headers=_headers(raw))
    assert (ok.status_code, bad.status_code) == (200, 422)
    records = [r for r in caplog.records if "display_name_changed" in r.getMessage()]
    assert len(records) == 1  # non-vacuous: an app-module line was captured
    assert records[0].name == "app.modules.users.profile_service"
    assert str(user.id) in records[0].getMessage()
    assert "zznamemarker" not in caplog.text
