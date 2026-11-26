"""GET /api/v1/auth/orgs for super-admin holders (USR-40, ADR-0030 §8): `access`, live check."""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.entitlements.models import OrgEntitlement
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.sessions.models import AccountSession
from app.modules.users.models import User
from tests.auth_factories import (
    ORIGIN,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    make_member_role,
)
from tests.rbac_factories import assign, make_org, make_role, super_admin_role

ORGS = "/api/v1/auth/orgs"
TOKEN = "/api/v1/auth/token"
PAST = datetime.now(UTC) - timedelta(days=1)


@dataclass
class World:
    app: App
    member: Role
    designated: Role
    holder: User
    raw: str
    account: AccountSession
    designated_assignment: RoleAssignment
    admin_assignment: RoleAssignment
    other: User  # an ordinary user whose assignments make orgs "of the app"


async def _world(db: AsyncSession) -> World:
    app = await make_login_app(db, "calechip", org_mode="required")
    member = await make_member_role(db, app)
    designated = await make_role(db, app, "calechip:super-admin")
    app.super_admin_role_id = designated.id
    await db.flush()
    holder = await make_login_user(db)
    designated_assignment = await assign(db, holder, designated)
    admin_assignment = await assign(db, holder, await super_admin_role(db))
    raw, _, (account,) = await make_device(db, holder)
    other = await make_login_user(db)
    return World(
        app,
        member,
        designated,
        holder,
        raw,
        account,
        designated_assignment,
        admin_assignment,
        other,
    )


async def _app_org(db: AsyncSession, w: World, name: str, **kwargs: Any) -> Organization:
    """An org of the app through another user's org-scope assignment."""
    org = await make_org(db, name=name, **kwargs)
    await assign(db, w.other, w.member, org_id=org.id)
    return org


async def _list(client: AsyncClient, w: World, *, raw: str | None = None, **params: Any) -> Any:
    return await client.get(
        ORGS,
        params={"account_id": str(w.account.id), **params},
        headers=auth_headers(app_key="calechip", cookie=raw or w.raw),
    )


def _items(resp: Any) -> list[tuple[str, str, list[str]]]:
    assert resp.status_code == 200, resp.text
    return [(i["name"], i["access"], i["roles"]) for i in resp.json()["items"]]


async def _plain_user(db: AsyncSession, w: World) -> tuple[User, str, AccountSession]:
    user = await make_login_user(db)
    await assign(db, user, w.member)
    raw, _, (account,) = await make_device(db, user)
    return user, raw, account


async def test_holder_sees_member_and_app_orgs(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    owner = await make_role(db_session, w.app, "calechip:org-owner")
    a = await make_org(db_session, name="A")
    await assign(db_session, w.holder, owner, org_id=a.id)
    await _app_org(db_session, w, "B")
    c = await make_org(db_session, name="C")
    db_session.add(OrgEntitlement(org_id=c.id, app_id=w.app.id, plan="pro", limits={}))
    await db_session.flush()
    resp = await _list(client, w)
    assert _items(resp) == [
        ("A", "member", ["calechip:org-owner"]),
        ("B", "super_admin", []),
        ("C", "super_admin", []),
    ]
    assert resp.json()["total"] == 3


async def test_holder_member_org_stays_member(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await _app_org(db_session, w, "Shared")
    await assign(db_session, w.holder, w.member, org_id=org.id)
    resp = await _list(client, w)
    assert _items(resp) == [("Shared", "member", ["calechip:member"])]
    assert resp.json()["total"] == 1


async def test_holder_excludes_other_app_orgs(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    stoktok = await make_login_app(db_session, "stoktok")
    viewer = await make_role(db_session, stoktok, "stoktok:viewer")
    by_role = await make_org(db_session, name="ByRole")
    await assign(db_session, w.other, viewer, org_id=by_role.id)
    by_ent = await make_org(db_session, name="ByEnt")
    db_session.add(OrgEntitlement(org_id=by_ent.id, app_id=stoktok.id, plan="pro", limits={}))
    await db_session.flush()
    resp = await _list(client, w)
    assert _items(resp) == []
    assert resp.json()["total"] == 0


async def test_holder_excludes_suspended_app_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    await _app_org(db_session, w, "Gone", status="suspended")
    assert _items(await _list(client, w)) == []


async def test_holder_excludes_org_with_only_expired_assignments(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await make_org(db_session, name="Old")
    await assign(db_session, w.other, w.member, org_id=org.id, expires_at=PAST)
    assert _items(await _list(client, w)) == []


async def test_holder_excludes_disabled_entitlement(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await make_org(db_session, name="Off")
    db_session.add(
        OrgEntitlement(org_id=org.id, app_id=w.app.id, plan="pro", limits={}, enabled=False)
    )
    await db_session.flush()
    assert _items(await _list(client, w)) == []


async def test_holder_suspended_member_org_listed_as_member(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await make_org(db_session, name="Mine", status="suspended")
    await assign(db_session, w.holder, w.member, org_id=org.id)
    resp = await _list(client, w)
    assert _items(resp) == [("Mine", "member", ["calechip:member"])]
    assert resp.json()["items"][0]["status"] == "suspended"


async def test_holder_expired_member_role_becomes_super_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await _app_org(db_session, w, "A")
    await assign(db_session, w.holder, w.member, org_id=org.id, expires_at=PAST)
    assert _items(await _list(client, w)) == [("A", "super_admin", [])]


async def test_non_holder_sees_no_app_orgs(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(db_session)
    user, raw, account = await _plain_user(db_session, w)
    mine = await make_org(db_session, name="Mine")
    await assign(db_session, user, w.member, org_id=mine.id)
    await _app_org(db_session, w, "Other")
    w.account = account
    resp = await _list(client, w, raw=raw)
    assert _items(resp) == [("Mine", "member", ["calechip:member"])]
    assert resp.json()["total"] == 1


async def test_non_staff_with_designated_role_sees_member_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    await db_session.delete(w.admin_assignment)
    await db_session.flush()
    mine = await make_org(db_session, name="Mine")
    await assign(db_session, w.holder, w.member, org_id=mine.id)
    await _app_org(db_session, w, "Other")
    assert _items(await _list(client, w)) == [("Mine", "member", ["calechip:member"])]


@pytest.mark.parametrize("how", ["deleted", "expired", "staff_deleted", "cleared"])
async def test_holder_loses_access_next_call(
    client: AsyncClient, db_session: AsyncSession, how: str
) -> None:
    w = await _world(db_session)
    mine = await make_org(db_session, name="A")
    await assign(db_session, w.holder, w.member, org_id=mine.id)
    await _app_org(db_session, w, "B")
    c = await make_org(db_session, name="C")
    db_session.add(OrgEntitlement(org_id=c.id, app_id=w.app.id, plan="pro", limits={}))
    await db_session.flush()
    first = await _list(client, w)
    assert [n for n, _, _ in _items(first)] == ["A", "B", "C"]

    if how == "deleted":
        await db_session.delete(w.designated_assignment)
    elif how == "expired":
        w.designated_assignment.expires_at = PAST
    elif how == "staff_deleted":
        await db_session.delete(w.admin_assignment)
    else:
        w.app.super_admin_role_id = None
    await db_session.flush()

    second = await _list(client, w)
    assert _items(second) == [("A", "member", ["calechip:member"])]
    assert second.json()["total"] == 1


async def test_holder_pagination_total_and_order(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    for name in ("B", "D"):
        org = await make_org(db_session, name=name)
        await assign(db_session, w.holder, w.member, org_id=org.id)
    for name in ("A", "C", "E"):
        await _app_org(db_session, w, name)
    seen: list[tuple[str, str, list[str]]] = []
    for offset in (0, 2, 4):
        resp = await _list(client, w, limit=2, offset=offset)
        assert resp.json()["total"] == 5
        seen += _items(resp)
    assert [(n, a) for n, a, _ in seen] == [
        ("A", "super_admin"),
        ("B", "member"),
        ("C", "super_admin"),
        ("D", "member"),
        ("E", "super_admin"),
    ]


async def test_holder_offset_past_member_orgs(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    org = await make_org(db_session, name="A")
    await assign(db_session, w.holder, w.member, org_id=org.id)
    for name in ("B", "C"):
        await _app_org(db_session, w, name)
    resp = await _list(client, w, limit=10, offset=1)
    assert _items(resp) == [("B", "super_admin", []), ("C", "super_admin", [])]
    assert resp.json()["total"] == 3


async def _statements(db: AsyncSession, call: Callable[[], Any]) -> tuple[Any, list[str]]:
    statements: list[str] = []
    conn = db.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: Any) -> None:
        statements.append(args[2])

    event.listen(conn, "before_cursor_execute", _record)
    try:
        resp = await call()
    finally:
        event.remove(conn, "before_cursor_execute", _record)
    return resp, statements


async def test_holder_query_count_constant(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(db_session)
    org = await make_org(db_session, name="M")
    await assign(db_session, w.holder, w.member, org_id=org.id)
    for name in ("B", "C", "D"):
        await _app_org(db_session, w, name)
    await _list(client, w)  # warm-up: the first request loads the CORS origins
    one, few = await _statements(db_session, lambda: _list(client, w, limit=1))
    many, lots = await _statements(db_session, lambda: _list(client, w, limit=50))
    assert one.status_code == many.status_code == 200
    assert len(many.json()["items"]) == 4
    assert len(few) == len(lots)
    pages = [s for s in lots if "FROM organizations" in s and "LIMIT" in s]
    counts = [s for s in lots if "count(*)" in s and "FROM organizations" in s]
    assert len(pages) == 1 and len(counts) == 1


async def test_non_holder_query_count_constant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    user, raw, account = await _plain_user(db_session, w)
    for name in ("A", "B", "C"):
        org = await make_org(db_session, name=name)
        await assign(db_session, user, w.member, org_id=org.id)
    w.account = account
    await _list(client, w, raw=raw)  # warm-up: the first request loads the CORS origins
    one, few = await _statements(db_session, lambda: _list(client, w, raw=raw, limit=1))
    many, lots = await _statements(db_session, lambda: _list(client, w, raw=raw, limit=50))
    assert one.status_code == many.status_code == 200
    assert len(many.json()["items"]) == 3
    assert len(few) == len(lots)
    assert not any("org_entitlements" in s for s in lots)


async def test_holder_unverified_403(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(db_session)
    holder = await make_login_user(db_session, verified=False)
    await assign(db_session, holder, w.designated)
    await assign(db_session, holder, await super_admin_role(db_session))
    raw, _, (account,) = await make_device(db_session, holder)
    org = await _app_org(db_session, w, "Secret")
    w.account = account
    resp = await _list(client, w, raw=raw)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "email_unverified"
    assert org.name not in resp.text


async def test_holder_bad_account_401(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(db_session)
    resp = await client.get(
        ORGS,
        params={"account_id": str(uuid.uuid4())},
        headers=auth_headers(app_key="calechip", cookie=w.raw),
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"


async def test_holder_on_admin_app_422(client: AsyncClient, db_session: AsyncSession) -> None:
    w = await _world(db_session)
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.origins = [ORIGIN]
    await db_session.flush()
    resp = await client.get(
        ORGS,
        params={"account_id": str(w.account.id)},
        headers=auth_headers(app_key="admin", cookie=w.raw),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "org_not_supported"


async def test_holder_list_not_audited_and_logged(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The migrations' `fileConfig` (session fixture) disables loggers that already exist.
    monkeypatch.setattr(
        logging.getLogger("app.modules.organizations.auth_service"), "disabled", False
    )
    w = await _world(db_session)
    for name in ("A", "B", "C"):
        await _app_org(db_session, w, name)
    before = await db_session.scalar(select(func.count()).select_from(AuditLog))
    with caplog.at_level(logging.DEBUG):
        resp = await _list(client, w)
    assert resp.status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == before
    lines = [r.getMessage() for r in caplog.records if "orgs_listed" in r.getMessage()]
    assert lines == [f"orgs_listed access=super_admin user_id={w.holder.id} app=calechip total=3"]
    assert "@" not in lines[0]


async def test_every_super_admin_item_is_mintable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    await _app_org(db_session, w, "ByRole")
    by_ent = await make_org(db_session, name="ByEnt")
    db_session.add(OrgEntitlement(org_id=by_ent.id, app_id=w.app.id, plan="pro", limits={}))
    await db_session.flush()
    resp = await _list(client, w)
    items = resp.json()["items"]
    assert [i["access"] for i in items] == ["super_admin", "super_admin"]
    headers = auth_headers(app_key="calechip", cookie=w.raw)
    for item in items:
        body = {"audience": "calechip", "account_id": str(w.account.id), "org_id": item["id"]}
        minted = await client.post(TOKEN, json=body, headers=headers)
        assert minted.status_code == 200, minted.text
        claims = jwt.decode(minted.json()["access_token"], options={"verify_signature": False})
        assert claims["org"] == item["id"] and claims["org_access"] == "super_admin"


async def test_excluded_orgs_are_not_mintable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(db_session)
    suspended = await _app_org(db_session, w, "Gone", status="suspended")
    stoktok = await make_login_app(db_session, "stoktok")
    foreign = await make_org(db_session, name="Foreign")
    await assign(
        db_session, w.other, await make_role(db_session, stoktok, "stoktok:v"), org_id=foreign.id
    )
    assert _items(await _list(client, w)) == []
    headers = auth_headers(app_key="calechip", cookie=w.raw)
    body = {"audience": "calechip", "account_id": str(w.account.id), "org_id": str(suspended.id)}
    resp = await client.post(TOKEN, json=body, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "org_access_denied"
