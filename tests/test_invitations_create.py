"""POST /api/v1/auth/orgs/{org_id}/invitations (USR-06b): permissions, roles, storage, limits."""

import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.auth.models import EmailSendLog
from app.modules.auth.throttle import EmailHasher
from app.modules.email.dispatcher import EmailDispatcher
from app.modules.rbac.models import App
from app.modules.rbac.service import RoleCatalogService
from tests.auth_factories import HMAC_KEY, ORIGIN, auth_headers, make_login_app, sha256
from tests.conftest import MakeClient
from tests.fakes import FakeEmailSender
from tests.invitation_factories import (
    CREATE_URL,
    Env,
    Person,
    create_body,
    invitations,
    link_token,
    person,
    setup_calechip,
    token_hash,
)
from tests.rbac_factories import assign, grant, make_org, make_permission, make_role

INVITEE = "invitee@example.com"


def _url(org_id: uuid.UUID) -> str:
    return CREATE_URL.format(org_id=org_id)


def _error(resp: Any) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


async def _invite(
    client: AsyncClient,
    env: Env,
    email: str = INVITEE,
    role: str = "calechip:org-viewer",
    *,
    who: Person | None = None,
    org_id: uuid.UUID | None = None,
) -> Any:
    who = who or env.inviter
    return await client.post(
        _url(org_id or env.org.id), json=create_body(who, email, role), headers=who.headers()
    )


async def _audit(db_session: AsyncSession, env: Env) -> list[AuditLog]:
    """This test's rows (committed rows of the concurrency test stay: audit_log is append-only)."""
    rows = await db_session.scalars(select(AuditLog).order_by(AuditLog.occurred_at, AuditLog.id))
    return [
        r
        for r in rows
        if r.action.startswith("organization.invitation_") and r.org_id == env.org.id
    ]


async def test_create_happy_201(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    resp = await _invite(client, env)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"id", "org_id", "role", "expires_at"}
    assert body["org_id"] == str(env.org.id) and body["role"] == "calechip:org-viewer"
    assert resp.headers["cache-control"] == "no-store"
    await email_dispatcher.join()
    (mail,) = fake_email.sent
    assert mail.to == INVITEE and mail.purpose == "invite"
    assert mail.user_id == env.inviter.user.id
    assert f"{ORIGIN}/accept-invitation#token=" in mail.body
    assert "Acme" in mail.body
    assert env.inviter.user.email not in mail.body + mail.subject


async def test_row_stores_hashes_only(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    resp = await _invite(client, env, "Invitee@Example.com ")
    assert resp.status_code == 201
    await email_dispatcher.join()
    (row,) = await invitations(db_session)
    assert row.token_hash == token_hash(link_token(fake_email.sent[0]))
    assert row.email_hmac == EmailHasher(HMAC_KEY).hmac("invitee@example.com")
    assert (row.org_id, row.invited_by, str(row.id)) == (
        env.org.id,
        env.inviter.user.id,
        resp.json()["id"],
    )
    columns = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'org_invitations'"
        )
    )
    assert not any("mail" in c[0] and c[0] != "email_hmac" for c in columns.all())
    ttl = (row.expires_at - row.created_at).total_seconds()
    assert ttl == 168 * 3600


async def test_link_uses_request_origin_and_fragment(
    make_client: MakeClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    other = "https://calechip.mmlabs.online"
    env.app.origins = [ORIGIN, other]
    await db_session.flush()
    async with make_client() as client:
        resp = await client.post(
            _url(env.org.id),
            json=create_body(env.inviter, INVITEE),
            headers=auth_headers(app_key="calechip", origin=other, cookie=env.inviter.raw),
        )
    assert resp.status_code == 201
    await email_dispatcher.join()
    link = re.search(r"https://\S+", fake_email.sent[0].body)
    assert link is not None
    assert link.group(0).startswith(f"{other}/accept-invitation#token=")
    assert "?" not in link.group(0)


async def test_reinvite_supersedes_previous(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    assert (await _invite(client, env)).status_code == 201
    first = (await invitations(db_session))[0]
    # The per-email interval is 1 minute: move the first send into the past.
    await db_session.execute(
        text("UPDATE email_send_log SET sent_at = sent_at - interval '2 minutes'")
    )
    assert (await _invite(client, env, role="calechip:org-owner")).status_code == 201
    rows = await invitations(db_session)
    assert len(rows) == 2
    await db_session.refresh(first)
    assert first.revoked_at is not None
    pending = [r for r in rows if r.revoked_at is None]
    assert len(pending) == 1 and pending[0].id != first.id
    actions = [r.action for r in await _audit(db_session, env)]
    assert actions.count("organization.invitation_revoked") == 1
    assert actions.count("organization.invitation_created") == 2
    revoked = next(r for r in await _audit(db_session, env) if r.action.endswith("revoked"))
    assert revoked.changes == {"reason": "superseded"}


# --- who may invite --------------------------------------------------------------------


async def test_create_unknown_org_403_same_as_non_member(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    stranger_org = await make_org(db_session)
    unknown = await _invite(client, env, org_id=uuid.uuid4())
    foreign = await _invite(client, env, org_id=stranger_org.id)
    assert unknown.status_code == foreign.status_code == 403
    assert unknown.json() == foreign.json()
    assert _error(unknown)["code"] == "org_access_denied"
    assert _error(unknown)["details"] == {}


async def test_create_suspended_org_403(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    env.org.status = "suspended"
    await db_session.flush()
    resp = await _invite(client, env)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_access_denied"


async def test_create_member_without_invite_perm_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    viewer = await person(db_session, env.viewer, org=env.org)
    resp = await _invite(client, env, who=viewer)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_invite_forbidden"


async def test_invite_perm_at_app_scope_only_does_not_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    # The default role itself carries `org:invite` at app scope; the org role does not.
    await grant(db_session, env.member, env.perms["org:invite"])
    staffish = await make_role(db_session, env.app, "calechip:staffish")
    await grant(db_session, staffish, env.perms["org:invite"])
    who = await person(db_session, env.viewer, org=env.org)
    await assign(db_session, who.user, env.member)
    await assign(db_session, who.user, staffish)
    resp = await _invite(client, env, who=who)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_invite_forbidden"


async def test_invite_perm_in_org_a_does_not_allow_org_b(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    org_b = await make_org(db_session)
    await assign(db_session, env.inviter.user, env.viewer, org_id=org_b.id)
    resp = await _invite(client, env, org_id=org_b.id)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "org_invite_forbidden"


async def test_create_unverified_403_and_bad_account_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    unverified = await person(db_session, env.member, verified=False)
    resp = await _invite(client, env, who=unverified)
    assert (resp.status_code, _error(resp)["code"]) == (403, "email_unverified")
    bad = await client.post(
        _url(env.org.id),
        json={**create_body(env.inviter, INVITEE), "account_id": str(uuid.uuid4())},
        headers=env.inviter.headers(),
    )
    assert (bad.status_code, _error(bad)["code"]) == (401, "session_invalid")


# --- which roles ------------------------------------------------------------------------


async def _manager(db_session: AsyncSession, env: Env) -> Person:
    """Holds `org:invite` and `data:read` in the org, nothing else."""
    role = await make_role(db_session, env.app, "calechip:org-manager")
    await grant(db_session, role, env.perms["org:invite"], env.perms["data:read"])
    return await person(db_session, role, org=env.org)


async def test_role_beyond_inviter_perms_403(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    manager = await _manager(db_session, env)
    ok = await _invite(client, env, who=manager)  # viewer: subset of the manager's permissions
    assert ok.status_code == 201
    resp = await _invite(client, env, "other@example.com", "calechip:org-owner", who=manager)
    assert resp.status_code == 403
    assert _error(resp)["code"] == "invite_role_forbidden"


async def test_member_inviter_cannot_invite_owner_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    manager = await _manager(db_session, env)
    resp = await _invite(client, env, role="calechip:org-owner", who=manager)
    assert resp.status_code == 403 and _error(resp)["code"] == "invite_role_forbidden"


async def test_owner_may_invite_owner_role(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    resp = await _invite(client, env, role="calechip:org-owner")
    assert resp.status_code == 201


async def test_subset_is_org_exact_app_scope_perms_do_not_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    manager = await _manager(db_session, env)
    extra = await make_role(db_session, env.app, "calechip:extra")
    await grant(db_session, extra, env.perms["org:edit"])
    await assign(db_session, manager.user, extra)  # `org:edit` at app scope only
    resp = await _invite(client, env, role="calechip:org-owner", who=manager)
    assert resp.status_code == 403 and _error(resp)["code"] == "invite_role_forbidden"


async def test_is_admin_role_403(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    wildcard = await make_permission(db_session, env.app, "calechip:*", is_admin=True)
    powerful = await make_role(db_session, env.app, "calechip:power")
    await grant(db_session, powerful, wildcard)
    # The inviter even holds the wildcard in the org, so the subset rule alone would pass.
    await assign(db_session, env.inviter.user, powerful, org_id=env.org.id)
    resp = await _invite(client, env, role="calechip:power")
    assert resp.status_code == 403 and _error(resp)["code"] == "invite_role_forbidden"


async def test_other_app_role_key_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    other = await make_login_app(db_session, "other", origins=[])
    await make_role(db_session, other, "other:viewer")
    resp = await _invite(client, env, role="other:viewer")
    assert resp.status_code == 422
    assert _error(resp)["code"] == "unknown_role"
    assert _error(resp)["details"] == [{"field": "role", "reason": "unknown_role"}]


async def test_unknown_role_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    resp = await _invite(client, env, role="calechip:nope")
    assert resp.status_code == 422 and _error(resp)["code"] == "unknown_role"


async def test_admin_app_context_403(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.origins = ["https://admin.mmlabs.online"]
    await db_session.flush()
    resp = await client.post(
        _url(env.org.id),
        json=create_body(env.inviter, INVITEE),
        headers=auth_headers(
            app_key="admin", origin="https://admin.mmlabs.online", cookie=env.inviter.raw
        ),
    )
    assert resp.status_code == 403 and _error(resp)["code"] == "origin_not_allowed"


async def test_create_org_less_app_422(client: AsyncClient, db_session: AsyncSession) -> None:
    await make_login_app(db_session, "stoktok")
    env_person = await person(db_session, None)
    resp = await client.post(
        _url(uuid.uuid4()),
        json=create_body(env_person, INVITEE),
        headers=env_person.headers("stoktok"),
    )
    assert resp.status_code == 422 and _error(resp)["code"] == "org_not_supported"


# --- S1 guard ----------------------------------------------------------------------------


async def test_create_unavailable_when_default_holds_org_permission(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    await grant(db_session, env.member, env.perms["data:read"])
    resp = await _invite(client, env)
    assert resp.status_code == 403 and _error(resp)["code"] == "org_invite_unavailable"
    assert await invitations(db_session) == []


async def test_create_ok_when_default_shares_app_scope_permission_with_owner(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    shared = await make_permission(db_session, env.app, "calechip:projects:read", scope="app")
    await grant(db_session, env.member, shared)
    await grant(db_session, env.owner, shared)
    resp = await _invite(client, env)
    assert resp.status_code == 201, resp.text


async def test_create_ok_after_build_default_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    await make_permission(db_session, env.app, "calechip:projects:read", scope="app")
    await grant(db_session, env.member, env.perms["data:read"])  # widened by hand
    built = await RoleCatalogService(db_session).build_default_role("calechip")
    assert "calechip:data:read" in built.removed
    resp = await _invite(client, env)
    assert resp.status_code == 201, resp.text


async def test_create_unavailable_when_join_not_open(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    env.app.join_policy = "closed"
    await db_session.flush()
    resp = await _invite(client, env)
    assert resp.status_code == 403 and _error(resp)["code"] == "org_invite_unavailable"


async def test_create_unavailable_owner_role_unset(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    env.app.org_owner_role_id = None
    await db_session.flush()
    resp = await _invite(client, env)
    assert resp.status_code == 403 and _error(resp)["code"] == "org_invite_unavailable"


# --- no oracle ---------------------------------------------------------------------------


async def test_response_same_for_known_and_unknown_email(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    known = await person(db_session, env.viewer, org=env.org)  # already a member of the org
    unknown = await _invite(client, env, "nobody@example.com")
    existing = await _invite(client, env, known.user.email)
    assert unknown.status_code == existing.status_code == 201
    assert set(unknown.json()) == set(existing.json())
    assert existing.json()["role"] == unknown.json()["role"]
    await email_dispatcher.join()
    assert sorted(m.to for m in fake_email.sent) == sorted(["nobody@example.com", known.user.email])


async def test_response_same_when_target_capped(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    first = await _invite(client, env)
    audit_before = len(await _audit(db_session, env))
    second = await _invite(client, env)  # within a minute: silent
    assert first.status_code == second.status_code == 201
    assert set(first.json()) == set(second.json())
    assert first.json()["id"] != second.json()["id"]
    await email_dispatcher.join()
    assert len(fake_email.sent) == 1
    assert len(await invitations(db_session)) == 1
    # Only the supersede of the first invitation is audited (S-1); no created row.
    after = await _audit(db_session, env)
    assert len(after) == audit_before + 1 and sum(r.action.endswith("revoked") for r in after) == 1
    # The 3 per 24 h limit, across orgs.
    await db_session.execute(
        text("UPDATE email_send_log SET sent_at = sent_at - interval '3 hours'")
    )
    email_hmac = (await invitations(db_session))[0].email_hmac
    for hours in (2, 1):
        db_session.add(
            EmailSendLog(
                email_hmac=email_hmac,
                purpose="invite",
                sent_at=(await db_session.scalar(select(func.now()))) - timedelta(hours=hours),
            )
        )
    await db_session.flush()
    third = await _invite(client, env)
    assert third.status_code == 201
    await email_dispatcher.join()
    assert len(fake_email.sent) == 1
    assert len(await invitations(db_session)) == 1


async def test_target_limited_reinvite_supersedes_and_audits(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    assert (await _invite(client, env, role="calechip:org-owner")).status_code == 201
    resp = await _invite(client, env)  # within a minute: silent, role viewer
    assert resp.status_code == 201 and resp.json()["role"] == "calechip:org-viewer"
    rows = await invitations(db_session)
    assert len(rows) == 1 and rows[0].revoked_at is not None
    revoked = [r for r in await _audit(db_session, env) if r.action.endswith("revoked")]
    assert len(revoked) == 1 and revoked[0].changes == {"reason": "superseded"}


async def test_create_does_not_query_users_by_email() -> None:
    package = Path(__file__).parent.parent / "app" / "modules" / "invitations"
    source = "\n".join(p.read_text() for p in package.glob("*.py"))
    assert "get_by_email" not in source
    assert "User.email" not in source


# --- limits ------------------------------------------------------------------------------


async def test_per_inviter_cap_403(make_client: MakeClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    async with make_client(invitation_max_per_inviter_per_day=1) as client:
        assert (await _invite(client, env, "a@example.com")).status_code == 201
        resp = await _invite(client, env, "b@example.com")
    assert resp.status_code == 403 and _error(resp)["code"] == "invitation_limit_reached"


async def test_per_org_pending_cap_403(make_client: MakeClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    other_owner = await person(db_session, env.member)
    await assign(db_session, other_owner.user, env.owner, org_id=env.org.id)
    async with make_client(invitation_max_pending_per_org=1) as client:
        assert (await _invite(client, env, "a@example.com")).status_code == 201
        resp = await _invite(client, env, "b@example.com", who=other_owner)
    assert resp.status_code == 403 and _error(resp)["code"] == "invitation_limit_reached"


async def test_create_rate_limited_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    async with make_client(invitation_create_rate_limit=1) as client:
        await _invite(client, env)
        resp = await _invite(client, env, "b@example.com")
    assert resp.status_code == 429 and _error(resp)["code"] == "rate_limited"


async def test_global_cap_counts_invites_and_answers_429(
    make_client: MakeClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    async with make_client(email_daily_cap=5) as client:  # invite share: max(1, 5 * 10 // 100)
        first = await _invite(client, env, "a@example.com")
        second = await _invite(client, env, "b@example.com")
    assert first.status_code == 201
    assert second.status_code == 429 and _error(second)["code"] == "rate_limited"
    assert second.headers["Retry-After"] == "3600"
    await email_dispatcher.join()
    assert [m.to for m in fake_email.sent] == ["a@example.com"]
    assert len(await invitations(db_session)) == 1


async def test_exhausted_cap_answers_429_even_for_target_limited_address(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    env = await setup_calechip(db_session)
    async with make_client(email_daily_cap=5) as client:  # invite share: 1
        assert (await _invite(client, env, "a@example.com")).status_code == 201
        resp = await _invite(client, env, "a@example.com")  # within 60 s and cap used up
    assert resp.status_code == 429 and _error(resp)["code"] == "rate_limited"


# --- audit -------------------------------------------------------------------------------


async def test_create_audit_row(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    resp = await _invite(client, env)
    (row,) = await _audit(db_session, env)
    assert row.action == "organization.invitation_created"
    assert (row.actor_kind, row.actor_user_id) == ("user", env.inviter.user.id)
    assert (row.target_type, str(row.target_id)) == ("invitation", resp.json()["id"])
    assert (row.app_key, row.org_id) == ("calechip", env.org.id)
    assert row.changes is not None
    assert set(row.changes) == {"role_key", "expires_at"}
    assert row.changes["role_key"] == "calechip:org-viewer"


async def test_refusals_write_no_audit(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await setup_calechip(db_session)
    viewer = await person(db_session, env.viewer, org=env.org)
    assert (await _invite(client, env, who=viewer)).status_code == 403
    assert (await _invite(client, env, role="calechip:nope")).status_code == 422
    assert (await _invite(client, env, org_id=uuid.uuid4())).status_code == 403
    assert await _audit(db_session, env) == []
    assert await invitations(db_session) == []


async def test_audit_changes_have_no_email_or_token(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    await _invite(client, env)
    await db_session.execute(
        text("UPDATE email_send_log SET sent_at = sent_at - interval '2 minutes'")
    )
    await _invite(client, env)  # supersedes: writes a revoked row too
    await email_dispatcher.join()
    rows = await _audit(db_session, env)
    assert len(rows) == 3
    token = link_token(fake_email.sent[0])
    for row in rows:
        blob = f"{row.changes} {row.user_agent} {row.actor_label}"
        assert "@" not in blob and token not in blob
        assert sha256(INVITEE).hex() not in blob
        for invitation in await invitations(db_session):
            assert invitation.email_hmac.hex() not in blob
            assert invitation.token_hash.hex() not in blob
