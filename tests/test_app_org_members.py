"""App-facing org member read (USR-29, ADR-0025): `org-members:read` app credential."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.users.models import User
from tests.app_credential_factories import make_credential
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_app, make_org, make_role, make_user

ACTION = "organization.members_read"
SCOPE = "org-members:read"


def _url(key: str, org_id: object, query: str = "") -> str:
    return f"/api/v1/apps/{key}/orgs/{org_id}/members{query}"


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


class Setup:
    app: App
    org: Organization
    app_key: str
    org_id: uuid.UUID
    raw: str
    key_id: str
    manager: Role
    member: Role


async def _setup(db: AsyncSession, **app_kwargs: object) -> Setup:
    s = Setup()
    s.app = await make_app(db, f"app-{uuid.uuid4().hex[:8]}", org_mode="required", **app_kwargs)
    s.org = await make_org(db)
    s.manager = await make_role(db, s.app, f"{s.app.key}:manager")
    s.member = await make_role(db, s.app, f"{s.app.key}:member")
    cred, s.raw = await make_credential(db, s.app, scope=SCOPE)
    s.key_id = cred.key_id
    s.app_key, s.org_id = s.app.key, s.org.id
    return s


async def _get(
    client: AsyncClient, s: Setup, query: str = "", org_id: object = None
) -> tuple[int, dict]:  # type: ignore[type-arg]
    resp = await client.get(_url(s.app_key, org_id or s.org_id, query), headers=_auth(s.raw))
    return resp.status_code, resp.json()


async def _audit(db: AsyncSession, action: str = ACTION) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.action == action)
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_lists_members_one_row_per_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    user = await make_user(db_session)
    user.display_name = "Ann"
    await assign(db_session, user, s.member, org_id=s.org_id)
    await assign(db_session, user, s.manager, org_id=s.org_id)
    uid, email, roles = user.id, user.email, sorted([s.manager.key, s.member.key])
    await db_session.commit()
    code, body = await _get(client, s)
    assert code == 200
    assert body["total"] == 1
    (item,) = body["items"]
    assert set(item) == {
        "user_id",
        "email",
        "email_verified",
        "display_name",
        "status",
        "org_roles",
        "joined_at",
    }
    assert item["user_id"] == str(uid)
    assert item["email"] == email
    assert item["email_verified"] is False
    assert item["display_name"] == "Ann"
    assert item["status"] == "active"
    assert item["org_roles"] == roles


async def test_total_counts_distinct_users(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    for _ in range(2):
        user = await make_user(db_session)
        await assign(db_session, user, s.member, org_id=s.org_id)
        await assign(db_session, user, s.manager, org_id=s.org_id)
    await db_session.commit()
    _, body = await _get(client, s)
    assert body["total"] == 2
    assert len(body["items"]) == 2


async def test_paging_is_stable_by_user_id(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    ids = []
    for _ in range(5):
        user = await make_user(db_session)
        ids.append(str(user.id))
        await assign(db_session, user, s.member, org_id=s.org_id)
    await db_session.commit()
    seen: list[str] = []
    for offset in (0, 2, 4):
        _, body = await _get(client, s, f"?limit=2&offset={offset}")
        assert body["total"] == 5
        seen += [i["user_id"] for i in body["items"]]
    assert seen == sorted(ids)


async def test_limit_over_100_is_422(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    code, _ = await _get(client, s, "?limit=101")
    assert code == 422


async def test_user_without_key_role_not_listed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    other = await make_app(db_session, f"other-{uuid.uuid4().hex[:8]}", org_mode="required")
    other_role = await make_role(db_session, other, f"{other.key}:member")
    other_org = await make_org(db_session)
    listed = await make_user(db_session)
    await assign(db_session, listed, s.member, org_id=s.org_id)
    app_scope, other_app, other_org_user = [await make_user(db_session) for _ in range(3)]
    await assign(db_session, app_scope, s.member)  # app scope (org_id NULL)
    await assign(db_session, other_app, other_role, org_id=s.org_id)
    await assign(db_session, other_org_user, s.member, org_id=other_org.id)
    lid = str(listed.id)
    await db_session.commit()
    _, body = await _get(client, s)
    assert [i["user_id"] for i in body["items"]] == [lid]
    assert body["total"] == 1


async def test_expired_assignment_ignored(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    past = datetime.now(UTC) - timedelta(days=1)
    only_expired, mixed = await make_user(db_session), await make_user(db_session)
    await assign(db_session, only_expired, s.member, org_id=s.org_id, expires_at=past)
    await assign(db_session, mixed, s.member, org_id=s.org_id, expires_at=past)
    active = await assign(db_session, mixed, s.manager, org_id=s.org_id)
    mid, manager_key = str(mixed.id), s.manager.key
    await db_session.commit()
    created = active.created_at
    _, body = await _get(client, s)
    (item,) = body["items"]
    assert item["user_id"] == mid
    assert item["org_roles"] == [manager_key]
    assert datetime.fromisoformat(item["joined_at"]) == created


async def test_disabled_user_listed_minimised(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    user = await make_user(db_session)
    user.display_name = "Gone"
    user.status = "disabled"
    await assign(db_session, user, s.member, org_id=s.org_id)
    await db_session.commit()
    _, body = await _get(client, s)
    (item,) = body["items"]
    assert item["status"] == "disabled"
    assert item["email"] is None
    assert item["display_name"] is None
    assert item["org_roles"] == [s.member.key]


async def test_unknown_unrelated_suspended_org_same_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    other = await make_app(db_session, f"other-{uuid.uuid4().hex[:8]}", org_mode="required")
    other_role = await make_role(db_session, other, f"{other.key}:member")
    unrelated = await make_org(db_session)
    await assign(db_session, await make_user(db_session), other_role, org_id=unrelated.id)
    suspended = await make_org(db_session, status="suspended")
    await assign(db_session, await make_user(db_session), s.member, org_id=suspended.id)
    ids = [uuid.uuid4(), unrelated.id, suspended.id]
    await db_session.commit()
    answers = []
    for org_id in ids:
        resp = await client.get(_url(s.app_key, org_id), headers=_auth(s.raw))
        assert resp.status_code == 404
        answers.append(resp.content)
    assert answers[0] == answers[1] == answers[2]
    assert b"org_access_denied" in answers[0]


async def test_org_with_only_expired_key_roles_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    await assign(
        db_session,
        await make_user(db_session),
        s.member,
        org_id=s.org_id,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    await db_session.commit()
    code, body = await _get(client, s)
    assert code == 404
    assert body["error"]["code"] == "org_access_denied"


async def test_org_mode_none_422(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await db_session.execute(update(App).where(App.id == s.app.id).values(org_mode="none"))
    await db_session.commit()
    code, body = await _get(client, s)
    assert code == 422
    assert body["error"]["code"] == "org_not_supported"


async def test_other_app_credential_403_mismatch_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    other = await make_app(db_session, f"other-{uuid.uuid4().hex[:8]}", org_mode="required")
    cred, raw = await make_credential(db_session, other, scope=SCOPE)
    label = f"app:{other.key}/{cred.key_id}"
    await db_session.commit()
    resp = await client.get(_url(s.app_key, s.org_id), headers=_auth(raw))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_credential_mismatch"
    (row,) = await _audit(db_session)
    assert (row.outcome, row.actor_label) == ("denied", label)
    assert row.changes == {"reason": "app_credential_mismatch"}


async def test_manifest_and_releases_keys_403_scope(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    raws = [
        (await make_credential(db_session, s.app, scope=sc))[1] for sc in ("manifest", "releases")
    ]
    await db_session.commit()
    for raw in raws:
        resp = await client.get(_url(s.app_key, s.org_id), headers=_auth(raw))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "app_credential_scope"
    rows = await _audit(db_session)
    assert [r.changes for r in rows] == [{"reason": "app_credential_scope"}] * 2


async def test_disabled_app_403(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await db_session.execute(update(App).where(App.id == s.app.id).values(status="disabled"))
    await db_session.commit()
    code, body = await _get(client, s)
    assert code == 403
    assert body["error"]["code"] == "app_disabled"


async def test_status_filter(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    active, disabled = await make_user(db_session), await make_user(db_session)
    disabled.status = "disabled"
    for user in (active, disabled):
        await assign(db_session, user, s.member, org_id=s.org_id)
    aid, did = str(active.id), str(disabled.id)
    await db_session.commit()
    _, body = await _get(client, s, "?status=disabled")
    assert [i["user_id"] for i in body["items"]] == [did] and body["total"] == 1
    _, body = await _get(client, s, "?status=active")
    assert [i["user_id"] for i in body["items"]] == [aid] and body["total"] == 1


async def test_status_filter_invalid_422(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    code, _ = await _get(client, s, "?status=nope")
    assert code == 422


async def test_updated_since_user_change_and_new_grant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    old = datetime.now(UTC) - timedelta(days=10)
    since = datetime.now(UTC) - timedelta(days=1)
    unchanged, changed, granted = [await make_user(db_session) for _ in range(3)]
    ras = [
        await assign(db_session, u, s.member, org_id=s.org_id)
        for u in (unchanged, changed, granted)
    ]
    ids = [str(u.id) for u in (unchanged, changed, granted)]
    for ra in ras[:2]:
        await db_session.execute(
            update(RoleAssignment).where(RoleAssignment.id == ra.id).values(created_at=old)
        )
    await db_session.execute(
        update(User).where(User.id.in_([unchanged.id, granted.id])).values(updated_at=old)
    )
    await db_session.execute(
        update(User).where(User.id == changed.id).values(updated_at=datetime.now(UTC))
    )
    await db_session.commit()
    _, body = await _get(client, s, "?updated_since=" + since.isoformat().replace("+", "%2B"))
    assert sorted(i["user_id"] for i in body["items"]) == sorted(ids[1:])
    assert body["total"] == 2


async def test_updated_since_requires_timezone_422_no_echo(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    resp = await client.get(
        _url(s.app_key, s.org_id, "?updated_since=2026-01-02T03:04:05"), headers=_auth(s.raw)
    )
    assert resp.status_code == 422
    assert "2026-01-02T03:04:05" not in resp.text


async def test_success_writes_one_audit_row_counts_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    await assign(db_session, await make_user(db_session), s.member, org_id=s.org_id)
    org_id = s.org_id
    await db_session.commit()
    await _get(client, s, "?limit=5")
    (row,) = await _audit(db_session)
    assert row.outcome == "success"
    assert row.actor_label == f"app:{s.app_key}/{s.key_id}"
    assert (row.target_type, row.target_id, row.org_id) == ("organization", org_id, org_id)
    assert row.changes == {
        "returned": 1,
        "total": 1,
        "limit": 5,
        "offset": 0,
        "status_filter": None,
        "updated_since_set": False,
    }
    assert "@" not in str(row.changes)


async def test_404_writes_one_denied_row(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    code, _ = await _get(client, s, org_id=uuid.uuid4())
    assert code == 404
    (row,) = await _audit(db_session)
    assert row.outcome == "denied"
    assert row.changes == {"reason": "org_access_denied"}


async def test_no_email_in_logs(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    user = await make_user(db_session)
    await assign(db_session, user, s.member, org_id=s.org_id)
    email = user.email
    await db_session.commit()
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler, root_level = Capture(), logging.root.level
    # Alembic's `fileConfig` (test session setup) disables app loggers: re-enable them.
    disabled = {
        n: lg
        for n, lg in logging.root.manager.loggerDict.items()
        if isinstance(lg, logging.Logger) and lg.disabled
    }
    for lg in disabled.values():
        lg.disabled = False
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.DEBUG)
    try:
        await _get(client, s)
        await _get(client, s, org_id=uuid.uuid4())
    finally:
        logging.root.removeHandler(handler)
        logging.root.setLevel(root_level)
        for lg in disabled.values():
            lg.disabled = True
    assert records  # the capture works
    assert email not in " ".join(records)


async def test_cache_control_no_store(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await assign(db_session, await make_user(db_session), s.member, org_id=s.org_id)
    await db_session.commit()
    resp = await client.get(_url(s.app_key, s.org_id), headers=_auth(s.raw))
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"


async def test_per_ip_rate_limit_429_before_auth(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    async with make_client(app_org_members_rate_limit=1) as c:
        first = await c.get(_url(s.app_key, s.org_id))
        second = await c.get(_url(s.app_key, s.org_id))
    assert (first.status_code, second.status_code) == (401, 429)
    assert int(second.headers["retry-after"]) >= 1


async def test_per_key_rate_limit_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    _, raw2 = await make_credential(db_session, s.app, scope=SCOPE)
    await db_session.commit()
    async with make_client(app_org_members_key_rate_limit=1) as c:
        codes = [
            (await c.get(_url(s.app_key, uuid.uuid4()), headers=_auth(raw))).status_code
            for raw in (s.raw, s.raw, raw2)
        ]
    assert codes == [404, 429, 404]
