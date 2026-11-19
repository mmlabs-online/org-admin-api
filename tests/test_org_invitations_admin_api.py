import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.invitations.models import OrgInvitation
from app.modules.invitations.repository import InvitationRepository
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Role, RoleAssignment
from tests.conftest import MakeClient
from tests.invitation_factories import make_invitation
from tests.rbac_factories import assign, make_app, make_org, make_role, make_user

KEYS = {
    "id",
    "app",
    "role_id",
    "role_key",
    "invited_by",
    "created_at",
    "expires_at",
    "accepted_at",
    "accepted_by",
    "revoked_at",
    "status",
}


def _url(org_id: object, invitation_id: object | None = None) -> str:
    base = f"/api/v1/organizations/{org_id}/invitations"
    return base if invitation_id is None else f"{base}/{invitation_id}"


@pytest.fixture
async def admin(make_client: MakeClient) -> AsyncIterator[AsyncClient]:
    async with make_client(admin_permissions=["admin:*"]) as c:
        yield c


async def _setup(db_session: AsyncSession) -> tuple[App, Role, Organization]:
    app = await make_app(db_session, "calechip")
    return app, await make_role(db_session, app, "calechip:viewer"), await make_org(db_session)


def _now() -> datetime:
    return datetime.now(UTC)


async def _fresh(db_session: AsyncSession, invitation: OrgInvitation) -> OrgInvitation:
    row = await db_session.scalar(
        select(OrgInvitation)
        .where(OrgInvitation.id == invitation.id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def _audit(db_session: AsyncSession) -> list[AuditLog]:
    rows = await db_session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "organization.invitation_revoked")
        .execution_options(populate_existing=True)
    )
    return list(rows)


# --- auth, 404, 422 ---------------------------------------------------------------


async def test_invitations_401_without_token(client: AsyncClient) -> None:
    assert (await client.get(_url(uuid.uuid4()))).status_code == 401
    assert (await client.delete(_url(uuid.uuid4(), uuid.uuid4()))).status_code == 401


async def test_invitations_403_without_organizations_read(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org = await make_org(db_session)
    async with make_client(admin_permissions=["admin:users:read"]) as c:
        resp = await c.get(_url(org.id))
    assert resp.status_code == 403
    assert resp.json()["error"]["details"]["required"] == "admin:organizations:read"


async def test_invitations_404_unknown_org(admin: AsyncClient) -> None:
    resp = await admin.get(_url(uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("suffix", ["?limit=101", "?limit=0", "?offset=-1", "?status=open"])
async def test_invitations_422_invalid_params(
    admin: AsyncClient, db_session: AsyncSession, suffix: str
) -> None:
    org = await make_org(db_session)
    assert (await admin.get(_url(org.id) + suffix)).status_code == 422


async def test_invitations_422_bad_uuid(admin: AsyncClient) -> None:
    assert (await admin.get(_url("not-a-uuid"))).status_code == 422


# --- list -------------------------------------------------------------------------


async def test_invitations_item_shape_exact_keys(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    _, role, org = await _setup(db_session)
    inviter = await make_user(db_session)
    now = _now()
    inv, _ = await make_invitation(
        db_session,
        org,
        role,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
        invited_by=inviter,
    )
    resp = await admin.get(_url(org.id))
    assert resp.status_code == 200
    (item,) = resp.json()["items"]
    assert set(item) == KEYS
    assert item["id"] == str(inv.id)
    assert item["app"] == "calechip" and item["role_key"] == "calechip:viewer"
    assert item["invited_by"] == str(inviter.id)
    assert item["status"] == "pending"


async def test_invitations_body_has_no_secrets(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    _, role, org = await _setup(db_session)
    now = _now()
    inv, raw = await make_invitation(
        db_session,
        org,
        role,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
    )
    text = (await admin.get(_url(org.id))).text
    assert raw not in text
    assert inv.token_hash.hex() not in text
    assert inv.email_hmac.hex() not in text
    assert "email" not in text and "token" not in text


async def test_invitations_cache_control_no_store(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    org = await make_org(db_session)
    assert (await admin.get(_url(org.id))).headers["cache-control"] == "no-store"


async def _four(db_session: AsyncSession) -> tuple[Organization, dict[str, OrgInvitation]]:
    _, role, org = await _setup(db_session)
    user = await make_user(db_session)
    now = _now()
    past, future = now - timedelta(days=5), now + timedelta(days=5)
    made = {
        "pending": await make_invitation(
            db_session, org, role, created_at=past + timedelta(days=1), expires_at=future
        ),
        "accepted": await make_invitation(
            db_session,
            org,
            role,
            created_at=past + timedelta(days=2),
            expires_at=future,
            accepted_by=user,
        ),
        "revoked": await make_invitation(
            db_session,
            org,
            role,
            created_at=past + timedelta(days=3),
            expires_at=future,
            revoked_at=now,
        ),
        "expired": await make_invitation(
            db_session, org, role, created_at=past, expires_at=now - timedelta(days=1)
        ),
    }
    return org, {k: v[0] for k, v in made.items()}


async def test_invitations_each_status(admin: AsyncClient, db_session: AsyncSession) -> None:
    org, made = await _four(db_session)
    items = (await admin.get(_url(org.id))).json()["items"]
    assert {i["id"]: i["status"] for i in items} == {
        str(inv.id): name for name, inv in made.items()
    }


async def test_invitations_status_precedence(admin: AsyncClient, db_session: AsyncSession) -> None:
    _, role, org = await _setup(db_session)
    user = await make_user(db_session)
    now = _now()
    created, expired = now - timedelta(days=5), now - timedelta(days=1)
    acc, _ = await make_invitation(
        db_session, org, role, created_at=created, expires_at=expired, accepted_by=user
    )
    rev, _ = await make_invitation(
        db_session,
        org,
        role,
        created_at=created,
        expires_at=expired,
        revoked_at=now - timedelta(days=2),
    )
    items = (await admin.get(_url(org.id))).json()["items"]
    assert {i["id"]: i["status"] for i in items} == {
        str(acc.id): "accepted",
        str(rev.id): "revoked",
    }


async def test_invitations_status_filter(admin: AsyncClient, db_session: AsyncSession) -> None:
    org, made = await _four(db_session)
    for name, inv in made.items():
        body = (await admin.get(_url(org.id), params={"status": name})).json()
        assert body["total"] == 1
        assert [i["id"] for i in body["items"]] == [str(inv.id)]


async def test_invitations_only_this_org_and_paging(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    _, role, org = await _setup(db_session)
    other = await make_org(db_session)
    now = _now()
    ids = []
    for i in range(3):
        inv, _ = await make_invitation(
            db_session,
            org,
            role,
            created_at=now - timedelta(days=10 - i),
            expires_at=now + timedelta(days=5),
        )
        ids.append(str(inv.id))
    stray, _ = await make_invitation(
        db_session,
        other,
        role,
        created_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=5),
    )
    expected = list(reversed(ids))
    full = (await admin.get(_url(org.id))).json()
    assert [i["id"] for i in full["items"]] == expected
    assert str(stray.id) not in {i["id"] for i in full["items"]}
    page = (await admin.get(_url(org.id), params={"limit": 1, "offset": 1})).json()
    assert [i["id"] for i in page["items"]] == expected[1:2]
    assert page["total"] == 3


# --- revoke -----------------------------------------------------------------------


async def test_revoke_401_403(make_client: MakeClient, db_session: AsyncSession) -> None:
    _, role, org = await _setup(db_session)
    now = _now()
    inv, _ = await make_invitation(
        db_session,
        org,
        role,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
    )
    async with make_client(admin_permissions=["admin:organizations:read"]) as c:
        resp = await c.delete(_url(org.id, inv.id))
    assert resp.status_code == 403
    assert resp.json()["error"]["details"]["required"] == "admin:organizations:write"
    assert (await _fresh(db_session, inv)).revoked_at is None


async def test_revoke_pending_204_and_audit(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    _, role, org = await _setup(db_session)
    actor = await make_user(db_session)
    now = _now()
    inv, raw = await make_invitation(
        db_session,
        org,
        role,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
    )
    async with make_client(admin_permissions=["admin:*"], admin_user=actor) as c:
        resp = await c.delete(_url(org.id, inv.id))
    assert resp.status_code == 204 and resp.content == b""
    assert (await _fresh(db_session, inv)).revoked_at is not None
    (row,) = await _audit(db_session)
    assert row.target_id == inv.id and row.org_id == org.id and row.app_key == "calechip"
    assert row.changes == {"reason": "staff"}
    assert row.actor_user_id == actor.id
    dump = f"{row.changes} {row.target_type} {row.app_key}"
    assert inv.token_hash.hex() not in dump and inv.email_hmac.hex() not in dump and raw not in dump


async def test_revoke_then_not_acceptable(admin: AsyncClient, db_session: AsyncSession) -> None:
    app, role, org = await _setup(db_session)
    now = _now()
    inv, _ = await make_invitation(
        db_session,
        org,
        role,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
    )
    repo = InvitationRepository(db_session)
    assert await repo.get_pending_for_update(inv.token_hash, app.id) is not None
    assert (await admin.delete(_url(org.id, inv.id))).status_code == 204
    assert await repo.get_pending_for_update(inv.token_hash, app.id) is None
    (item,) = (await admin.get(_url(org.id))).json()["items"]
    assert item["status"] == "revoked"


async def test_revoke_other_org_404(admin: AsyncClient, db_session: AsyncSession) -> None:
    _, role, org = await _setup(db_session)
    other = await make_org(db_session)
    now = _now()
    inv, _ = await make_invitation(
        db_session,
        other,
        role,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
    )
    resp = await admin.delete(_url(org.id, inv.id))
    assert resp.status_code == 404
    assert (await _fresh(db_session, inv)).revoked_at is None
    assert await _audit(db_session) == []


async def test_revoke_unknown_invitation_404(admin: AsyncClient, db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    resp = await admin.delete(_url(org.id, uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "Invitation not found"


async def test_revoke_unknown_org_404(admin: AsyncClient) -> None:
    assert (await admin.delete(_url(uuid.uuid4(), uuid.uuid4()))).status_code == 404


async def test_revoke_422_bad_ids(admin: AsyncClient, db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    assert (await admin.delete(_url(org.id, "nope"))).status_code == 422


async def test_revoke_accepted_409(admin: AsyncClient, db_session: AsyncSession) -> None:
    _, role, org = await _setup(db_session)
    user = await make_user(db_session)
    now = _now()
    inv, _ = await make_invitation(
        db_session,
        org,
        role,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
        accepted_by=user,
    )
    resp = await admin.delete(_url(org.id, inv.id))
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"][0]["reason"] == "invitation_accepted"
    assert (await _fresh(db_session, inv)).revoked_at is None
    assert await _audit(db_session) == []


async def test_revoke_already_revoked_204_no_second_audit(
    admin: AsyncClient, db_session: AsyncSession
) -> None:
    _, role, org = await _setup(db_session)
    now = _now()
    inv, _ = await make_invitation(
        db_session,
        org,
        role,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
    )
    assert (await admin.delete(_url(org.id, inv.id))).status_code == 204
    first = (await _fresh(db_session, inv)).revoked_at
    assert (await admin.delete(_url(org.id, inv.id))).status_code == 204
    assert (await _fresh(db_session, inv)).revoked_at == first
    assert len(await _audit(db_session)) == 1


async def test_revoke_expired_204(admin: AsyncClient, db_session: AsyncSession) -> None:
    _, role, org = await _setup(db_session)
    now = _now()
    inv, _ = await make_invitation(
        db_session,
        org,
        role,
        created_at=now - timedelta(days=5),
        expires_at=now - timedelta(days=1),
    )
    assert (await admin.delete(_url(org.id, inv.id))).status_code == 204
    assert len(await _audit(db_session)) == 1
    (item,) = (await admin.get(_url(org.id))).json()["items"]
    assert item["status"] == "revoked"


async def test_revoke_keeps_role_assignments(admin: AsyncClient, db_session: AsyncSession) -> None:
    _, role, org = await _setup(db_session)
    await assign(db_session, await make_user(db_session), role, org_id=org.id)
    now = _now()
    inv, _ = await make_invitation(
        db_session,
        org,
        role,
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=1),
    )
    before = await db_session.scalar(select(func.count()).select_from(RoleAssignment))
    assert (await admin.delete(_url(org.id, inv.id))).status_code == 204
    assert await db_session.scalar(select(func.count()).select_from(RoleAssignment)) == before
