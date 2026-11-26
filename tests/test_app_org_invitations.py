"""App-facing pending invitation read (USR-31, ADR-0025 amendment): `org-members:read` key."""

import logging
import os
import uuid
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.invitations.models import OrgInvitation
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Role
from tests.app_credential_factories import make_credential
from tests.conftest import MakeClient
from tests.invitation_factories import make_invitation
from tests.rbac_factories import assign, make_app, make_org, make_role, make_user

ACTION = "organization.invitations_read"
SCOPE = "org-members:read"
FIELDS = {"id", "role", "created_at", "expires_at"}


def _url(key: str, org_id: object, query: str = "") -> str:
    return f"/api/v1/apps/{key}/orgs/{org_id}/invitations{query}"


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
    """An org related to the app (one member holds `member`), plus a runtime key."""
    s = Setup()
    s.app = await make_app(db, f"app-{uuid.uuid4().hex[:8]}", org_mode="required", **app_kwargs)
    s.org = await make_org(db)
    s.manager = await make_role(db, s.app, f"{s.app.key}:manager")
    s.member = await make_role(db, s.app, f"{s.app.key}:member")
    await assign(db, await make_user(db), s.member, org_id=s.org.id)
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


async def _invite(
    db: AsyncSession,
    org: Organization,
    role: Role,
    *,
    created_ago: timedelta = timedelta(hours=1),
    expires_in: timedelta = timedelta(days=5),
    **kwargs: object,
) -> tuple[OrgInvitation, str]:
    """Times are set on the database clock (never `datetime.now()`)."""
    now = await db.scalar(select(func.now()))
    assert now is not None
    inv, raw = await make_invitation(
        db,
        org,
        role,
        created_at=now - timedelta(days=30),
        expires_at=now - timedelta(days=29),
        **kwargs,  # type: ignore[arg-type]
    )
    await db.execute(
        update(OrgInvitation)
        .where(OrgInvitation.id == inv.id)
        .values(created_at=func.now() - created_ago, expires_at=func.now() + expires_in)
    )
    return inv, raw


async def test_lists_pending_invitations_of_key_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    other = await make_app(db_session, f"other-{uuid.uuid4().hex[:8]}", org_mode="required")
    other_role = await make_role(db_session, other, f"{other.key}:member")
    await assign(db_session, await make_user(db_session), other_role, org_id=s.org_id)
    other_org = await make_org(db_session)
    await assign(db_session, await make_user(db_session), s.member, org_id=other_org.id)
    user = await make_user(db_session)
    a, _ = await _invite(db_session, s.org, s.manager, created_ago=timedelta(hours=2))
    b, _ = await _invite(db_session, s.org, s.member, created_ago=timedelta(hours=1))
    await _invite(db_session, s.org, s.member, accepted_by=user)
    await _invite(
        db_session, s.org, s.member, revoked_at=await db_session.scalar(select(func.now()))
    )
    await _invite(db_session, s.org, s.member, expires_in=-timedelta(seconds=1))
    await _invite(db_session, s.org, other_role)
    await _invite(db_session, other_org, s.member)
    expected = [(str(a.id), s.manager.key), (str(b.id), s.member.key)]
    await db_session.commit()
    code, body = await _get(client, s)
    assert code == 200
    assert [(i["id"], i["role"]) for i in body["items"]] == expected
    assert body["total"] == 2


async def test_expiry_uses_db_clock(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await _invite(db_session, s.org, s.member, expires_in=-timedelta(seconds=1))
    live, _ = await _invite(db_session, s.org, s.member, expires_in=timedelta(minutes=5))
    live_id = str(live.id)
    await db_session.commit()
    _, body = await _get(client, s)
    assert [i["id"] for i in body["items"]] == [live_id]


async def test_paging_stable_by_created_at_then_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    invs = [
        (await _invite(db_session, s.org, s.member, created_ago=timedelta(hours=h)))[0]
        for h in (3, 2, 2, 1)
    ]
    # two share a created_at exactly
    await db_session.execute(
        update(OrgInvitation)
        .where(OrgInvitation.id == invs[2].id)
        .values(created_at=select(OrgInvitation.created_at).where(OrgInvitation.id == invs[1].id))
    )
    await db_session.flush()
    tied = sorted([invs[1].id, invs[2].id])
    expected = [str(invs[0].id), str(tied[0]), str(tied[1]), str(invs[3].id)]
    await db_session.commit()
    _, p1 = await _get(client, s, "?limit=2&offset=0")
    _, p2 = await _get(client, s, "?limit=2&offset=2")
    assert [i["id"] for i in p1["items"] + p2["items"]] == expected
    assert p1["total"] == p2["total"] == 4


async def test_limit_over_100_is_422(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    code, _ = await _get(client, s, "?limit=101")
    assert code == 422


async def test_empty_list_for_related_org_without_invitations(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    code, body = await _get(client, s)
    assert code == 200
    assert (body["items"], body["total"]) == ([], 0)


async def test_item_has_exactly_four_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await _invite(db_session, s.org, s.member, invited_by=await make_user(db_session))
    await db_session.commit()
    _, body = await _get(client, s)
    (item,) = body["items"]
    assert set(item) == FIELDS


async def test_no_secret_in_response_logs_or_audit(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    manager_user = await make_user(db_session)
    inv, raw_token = await _invite(db_session, s.org, s.member, invited_by=manager_user)
    invitee = f"invitee-{uuid.uuid4().hex}@example.com"
    email_hmac = os.urandom(32)
    await db_session.execute(
        update(OrgInvitation).where(OrgInvitation.id == inv.id).values(email_hmac=email_hmac)
    )
    sentinels = [
        invitee,
        email_hmac.hex(),
        raw_token,
        inv.token_hash.hex(),
        manager_user.email,
        str(manager_user.id),
    ]
    org_id = str(s.org_id)
    wrong_cred, wrong_scope_raw = await make_credential(db_session, s.app, scope="manifest")
    wrong_key_id = wrong_cred.key_id
    await db_session.commit()
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())
            names.append(record.name)

    names: list[str] = []
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
        ok = await client.get(_url(s.app_key, s.org_id), headers=_auth(s.raw))
        denied = await client.get(_url(s.app_key, uuid.uuid4()), headers=_auth(s.raw))
        # a wrong-scope key makes `app_credentials.service` log a line carrying the key id
        await client.get(_url(s.app_key, s.org_id), headers=_auth(wrong_scope_raw))
    finally:
        logging.root.removeHandler(handler)
        logging.root.setLevel(root_level)
        for lg in disabled.values():
            lg.disabled = True
    assert (ok.status_code, denied.status_code) == (200, 404)
    logged = " ".join(records)
    assert records
    # non-vacuous: an app-module line (app_credentials.service, key id) was captured
    assert any(
        n.startswith("app.modules.app_credentials") and wrong_key_id in m
        for n, m in zip(names, records, strict=True)
    )
    assert org_id in logged  # the request lines (httpx) carry the org id
    rows = await _audit(db_session)
    assert rows  # the shared test session may drop the success row when the 404 rolls back
    for sentinel in sentinels:
        assert sentinel not in ok.text
        assert sentinel not in denied.text
        assert sentinel not in logged
        assert all(sentinel not in str(r.changes) for r in rows)


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
    await _invite(db_session, suspended, s.member)
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
    org = await make_org(db_session)
    await assign(
        db_session,
        await make_user(db_session),
        s.member,
        org_id=org.id,
        expires_at=(await db_session.scalar(select(func.now()))) - timedelta(hours=1),  # type: ignore[operator,arg-type]
    )
    await _invite(db_session, org, s.member)
    org_id = org.id
    await db_session.commit()
    code, body = await _get(client, s, org_id=org_id)
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


async def test_manifest_releases_usage_keys_403_scope(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    raws = [
        (await make_credential(db_session, s.app, scope=sc))[1]
        for sc in ("manifest", "releases", "usage:read")
    ]
    await db_session.commit()
    for raw in raws:
        resp = await client.get(_url(s.app_key, s.org_id), headers=_auth(raw))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "app_credential_scope"
    rows = await _audit(db_session)
    assert [r.changes for r in rows] == [{"reason": "app_credential_scope"}] * 3


async def test_disabled_app_403(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await db_session.execute(update(App).where(App.id == s.app.id).values(status="disabled"))
    await db_session.commit()
    code, body = await _get(client, s)
    assert code == 403
    assert body["error"]["code"] == "app_disabled"


async def test_success_writes_one_audit_row_counts_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    await _invite(db_session, s.org, s.member)
    org_id = s.org_id
    await db_session.commit()
    await _get(client, s, "?limit=5")
    (row,) = await _audit(db_session)
    assert row.outcome == "success"
    assert row.actor_label == f"app:{s.app_key}/{s.key_id}"
    assert (row.target_type, row.target_id, row.org_id) == ("organization", org_id, org_id)
    assert row.changes == {"returned": 1, "total": 1, "limit": 5, "offset": 0}
    assert await _audit(db_session, "organization.members_read") == []


async def test_404_writes_one_denied_row(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    code, _ = await _get(client, s, org_id=uuid.uuid4())
    assert code == 404
    (row,) = await _audit(db_session)
    assert row.outcome == "denied"
    assert row.changes == {"reason": "org_access_denied"}


async def test_cache_control_no_store(client: AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    resp = await client.get(_url(s.app_key, s.org_id), headers=_auth(s.raw))
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"


async def test_per_ip_bucket_shared_with_members_read(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    await db_session.commit()
    members = f"/api/v1/apps/{s.app_key}/orgs/{s.org_id}/members"
    async with make_client(app_org_members_rate_limit=1) as c:
        first = await c.get(members, headers=_auth(s.raw))
        second = await c.get(_url(s.app_key, s.org_id), headers=_auth(s.raw))
    assert (first.status_code, second.status_code) == (200, 429)
    assert int(second.headers["retry-after"]) >= 1


async def test_per_key_bucket_shared_with_members_read(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    s = await _setup(db_session)
    _, raw2 = await make_credential(db_session, s.app, scope=SCOPE)
    await db_session.commit()
    members = f"/api/v1/apps/{s.app_key}/orgs/{uuid.uuid4()}/members"
    async with make_client(app_org_members_key_rate_limit=1) as c:
        first = await c.get(members, headers=_auth(s.raw))
        second = await c.get(_url(s.app_key, uuid.uuid4()), headers=_auth(s.raw))
        third = await c.get(_url(s.app_key, uuid.uuid4()), headers=_auth(raw2))
    assert (first.status_code, second.status_code, third.status_code) == (404, 429, 404)
