"""POST /api/v1/auth/invitations/accept (USR-06b): bound to the invited email, single use,
re-checked state, atomic."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.main import create_app
from app.modules.audit.models import AuditLog
from app.modules.email.dispatcher import EmailDispatcher
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, RoleAssignment
from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.users.models import User
from tests.auth_factories import ORIGIN, auth_headers, make_device, make_login_app
from tests.conftest import MakeClient
from tests.fakes import FakeEmailSender
from tests.invitation_factories import (
    ACCEPT_URL,
    CREATE_URL,
    Env,
    Person,
    invitations,
    link_token,
    person,
    setup_calechip,
    token_hash,
)
from tests.rbac_factories import assign, grant, make_permission, make_role

INVITEE = "invitee@example.com"
TOKEN_URL = "/api/v1/auth/token"


def _error(resp: Any) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


async def _send(
    client: AsyncClient,
    env: Env,
    dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    email: str = INVITEE,
    role: str = "calechip:org-viewer",
) -> str:
    """Create an invitation through the API; the token from the mail."""
    resp = await client.post(
        CREATE_URL.format(org_id=env.org.id),
        json={"account_id": str(env.inviter.account.id), "email": email, "role": role},
        headers=env.inviter.headers(),
    )
    assert resp.status_code == 201, resp.text
    await dispatcher.join()
    return link_token(fake_email.sent[-1])


async def _accept(
    client: AsyncClient, who: Person, token: str, *, app_key: str = "calechip"
) -> Response:
    return await client.post(
        ACCEPT_URL,
        json={"account_id": str(who.account.id), "token": token},
        headers=who.headers(app_key),
    )


async def _invitee(db_session: AsyncSession, **kw: Any) -> Person:
    return await person(db_session, None, email=INVITEE, **kw)


async def _audit(db_session: AsyncSession, actor: Person, *actions: str) -> list[AuditLog]:
    """The rows of `actor` (the concurrency test commits rows; audit_log is append-only)."""
    rows = await db_session.scalars(select(AuditLog).order_by(AuditLog.occurred_at, AuditLog.id))
    return [r for r in rows if r.action in actions and r.actor_user_id == actor.user.id]


ACCEPT_ACTIONS = ("organization.invitation_accepted", "role_assignment.granted", "app.joined")


async def _consumed(db_session: AsyncSession) -> bool:
    (row,) = await invitations(db_session)
    await db_session.refresh(row)
    return row.accepted_at is not None


async def test_accept_happy_joins_and_grants(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    resp = await _accept(client, invitee, token)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"org_id": str(env.org.id), "roles": ["calechip:org-viewer"]}
    assert resp.headers["cache-control"] == "no-store"
    grants = list(
        await db_session.scalars(
            select(RoleAssignment).where(RoleAssignment.user_id == invitee.user.id)
        )
    )
    assert {(g.role_id, g.org_id) for g in grants} == {
        (env.member.id, None),  # B1: joined with the default role
        (env.viewer.id, env.org.id),
    }
    (row,) = await invitations(db_session)
    assert row.accepted_by == invitee.user.id and row.accepted_at is not None


async def test_accept_then_token_with_org_id(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    assert (await _accept(client, invitee, token)).status_code == 200
    resp = await client.post(
        TOKEN_URL,
        json={
            "audience": "calechip",
            "account_id": str(invitee.account.id),
            "org_id": str(env.org.id),
        },
        headers=invitee.headers(),
    )
    assert resp.status_code == 200, resp.text
    claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
    assert claims["org"] == str(env.org.id)
    assert claims["roles"] == ["calechip:member", "calechip:org-viewer"]


async def test_accept_existing_member_no_second_join(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await person(db_session, env.member, email=INVITEE)
    assert (await _accept(client, invitee, token)).status_code == 200
    actions = [r.action for r in await _audit(db_session, invitee, *ACCEPT_ACTIONS)]
    assert sorted(actions) == ["organization.invitation_accepted", "role_assignment.granted"]


async def test_accept_unverified_403_not_consumed(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session, verified=False)
    resp = await _accept(client, invitee, token)
    assert resp.status_code == 403 and _error(resp)["code"] == "email_unverified"
    assert not await _consumed(db_session)
    # The same (now rejected) request with a garbage token answers the same: nothing is probed.
    junk = await _accept(client, invitee, "not-a-token")
    assert junk.json() == resp.json()


async def test_accept_other_verified_account_403_not_consumed_then_right_account_ok(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    other = await person(db_session, None, email="someone.else@example.com")
    resp = await _accept(client, other, token)
    assert resp.status_code == 403 and _error(resp)["code"] == "invitation_email_mismatch"
    assert not await _consumed(db_session)
    assert await _audit(db_session, other, *ACCEPT_ACTIONS) == []
    right = await _invitee(db_session)
    assert (await _accept(client, right, token)).status_code == 200


async def test_accept_email_match_is_case_insensitive(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email, "Invitee@Example.COM")
    invitee = await _invitee(db_session)
    assert (await _accept(client, invitee, token)).status_code == 200


async def test_replayed_expired_superseded_unknown_other_app_identical_422(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    invitee = await _invitee(db_session)
    bodies: list[dict[str, Any]] = []

    async def check(resp: Response) -> None:
        assert resp.status_code == 422, resp.text
        assert _error(resp)["code"] == "token_invalid"
        assert _error(resp)["details"] == {}
        bodies.append(resp.json())

    # replayed
    token = await _send(client, env, email_dispatcher, fake_email)
    assert (await _accept(client, invitee, token)).status_code == 200
    await check(await _accept(client, invitee, token))
    # expired
    await db_session.execute(
        text(
            "UPDATE org_invitations SET created_at = now() - interval '2 days', "
            "expires_at = now() - interval '1 day'"
        )
    )
    await db_session.execute(
        text("UPDATE org_invitations SET accepted_at = NULL, accepted_by = NULL")
    )
    await check(await _accept(client, invitee, token))
    # superseded (a new invitation to the same address revokes the first)
    await db_session.execute(text("DELETE FROM org_invitations"))
    await db_session.execute(text("DELETE FROM email_send_log"))
    first = await _send(client, env, email_dispatcher, fake_email)
    await db_session.execute(
        text("UPDATE email_send_log SET sent_at = sent_at - interval '2 minutes'")
    )
    await _send(client, env, email_dispatcher, fake_email)
    await check(await _accept(client, invitee, first))
    # unknown
    await check(await _accept(client, invitee, "no-such-token"))
    # a token of another app
    other = await make_login_app(db_session, "other", origins=["https://other.mmlabs.online"])
    await make_role(db_session, other, "other:member")
    raw, _, (account,) = await make_device(db_session, invitee.user)  # same user, other device
    stranger = Person(invitee.user, raw, account)
    live = fake_email.sent[-1]
    resp = await client.post(
        ACCEPT_URL,
        json={"account_id": str(stranger.account.id), "token": link_token(live)},
        headers=auth_headers(
            app_key="other", origin="https://other.mmlabs.online", cookie=stranger.raw
        ),
    )
    await check(resp)
    assert all(b == bodies[0] for b in bodies)


async def test_accept_bad_account_401(client: AsyncClient, db_session: AsyncSession) -> None:
    await setup_calechip(db_session)
    invitee = await _invitee(db_session)
    resp = await client.post(
        ACCEPT_URL,
        json={"account_id": str(uuid.uuid4()), "token": "x"},
        headers=invitee.headers(),
    )
    assert resp.status_code == 401 and _error(resp)["code"] == "session_invalid"


async def test_accept_rate_limited_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    await setup_calechip(db_session)
    invitee = await _invitee(db_session)
    async with make_client(invitation_accept_rate_limit=1) as client:
        await _accept(client, invitee, "x")
        resp = await _accept(client, invitee, "y")
    assert resp.status_code == 429 and _error(resp)["code"] == "rate_limited"


# --- re-checks at accept ------------------------------------------------------------------


async def _expect_invalid_not_consumed(
    client: AsyncClient, db_session: AsyncSession, who: Person, token: str
) -> None:
    resp = await _accept(client, who, token)
    assert resp.status_code == 422 and _error(resp)["code"] == "token_invalid"
    assert not await _consumed(db_session)
    assert await _audit(db_session, who, *ACCEPT_ACTIONS) == []


async def test_accept_after_inviter_role_revoked_422(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    await db_session.execute(
        delete(RoleAssignment).where(
            RoleAssignment.user_id == env.inviter.user.id, RoleAssignment.org_id == env.org.id
        )
    )
    await _expect_invalid_not_consumed(client, db_session, invitee, token)


async def test_accept_after_role_gains_admin_perm_422(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    admin_perm = await make_permission(db_session, env.app, "calechip:org:danger", is_admin=True)
    await grant(db_session, env.viewer, admin_perm)
    await _expect_invalid_not_consumed(client, db_session, invitee, token)


async def test_accept_after_org_suspended_422(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    org = await db_session.get(Organization, env.org.id)
    assert org is not None
    org.status = "suspended"
    await db_session.flush()
    await _expect_invalid_not_consumed(client, db_session, invitee, token)


async def test_accept_after_inviter_disabled_422(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    env.inviter.user.status = "disabled"
    await db_session.flush()
    await _expect_invalid_not_consumed(client, db_session, invitee, token)


async def test_accept_after_role_gains_perm_the_inviter_lacks_422(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    report = await make_permission(db_session, env.app, "calechip:report:read")
    await grant(db_session, env.viewer, report)
    await _expect_invalid_not_consumed(client, db_session, invitee, token)


async def test_accept_unavailable_when_default_widened_not_consumed(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    await grant(db_session, env.member, env.perms["data:read"])
    resp = await _accept(client, invitee, token)
    assert resp.status_code == 403 and _error(resp)["code"] == "org_invite_unavailable"
    assert not await _consumed(db_session)
    assert await _audit(db_session, invitee, *ACCEPT_ACTIONS) == []


async def test_accept_closed_app_refused_not_consumed(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    env.app.join_policy = "closed"
    await db_session.flush()
    resp = await _accept(client, invitee, token)
    assert resp.status_code == 403 and _error(resp)["code"] == "org_invite_unavailable"
    assert not await _consumed(db_session)


# --- already held, audit ------------------------------------------------------------------


async def test_accept_role_already_held_consumes_and_audits(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await person(db_session, env.member, email=INVITEE)
    await assign(db_session, invitee.user, env.viewer, org_id=env.org.id)
    resp = await _accept(client, invitee, token)
    assert resp.status_code == 200
    assert resp.json()["roles"] == ["calechip:org-viewer"]
    assert await _consumed(db_session)
    (row,) = await _audit(db_session, invitee, *ACCEPT_ACTIONS)
    assert row.action == "organization.invitation_accepted"
    assert row.changes == {
        "role_key": "calechip:org-viewer",
        "invited_by": str(env.inviter.user.id),
        "granted": False,
    }


async def test_accept_audit_rows(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    invitee = await _invitee(db_session)
    assert (await _accept(client, invitee, token)).status_code == 200
    rows = {r.action: r for r in await _audit(db_session, invitee, *ACCEPT_ACTIONS)}
    assert set(rows) == set(ACCEPT_ACTIONS)
    accepted = rows["organization.invitation_accepted"]
    assert accepted.changes == {
        "role_key": "calechip:org-viewer",
        "invited_by": str(env.inviter.user.id),
        "granted": True,
    }
    assert (accepted.actor_user_id, accepted.target_type, accepted.org_id) == (
        invitee.user.id,
        "invitation",
        env.org.id,
    )
    granted = rows["role_assignment.granted"]
    assert granted.changes == {"role_key": "calechip:org-viewer", "org_id": str(env.org.id)}
    assert rows["app.joined"].org_id is None
    for row in rows.values():
        blob = f"{row.changes}"
        assert "@" not in blob and token not in blob


async def test_refusals_write_no_audit(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    email_dispatcher: EmailDispatcher,
) -> None:
    env = await setup_calechip(db_session)
    token = await _send(client, env, email_dispatcher, fake_email)
    other = await person(db_session, None, email="x@example.com")
    unverified = await person(db_session, None, email="y@example.com", verified=False)
    assert (await _accept(client, other, token)).status_code == 403
    assert (await _accept(client, unverified, token)).status_code == 403
    assert (await _accept(client, other, "junk")).status_code == 422
    assert await _audit(db_session, other, *ACCEPT_ACTIONS) == []
    assert await _audit(db_session, unverified, *ACCEPT_ACTIONS) == []


# --- concurrency --------------------------------------------------------------------------


@dataclass
class Race:
    app: Any
    env: Env
    people: list[Person]
    token: str


async def _cleanup(session: AsyncSession, race: Race) -> None:
    user_ids = [p.user.id for p in race.people] + [race.env.inviter.user.id]
    device_ids = (
        await session.scalars(
            select(AccountSession.device_session_id).where(AccountSession.user_id.in_(user_ids))
        )
    ).all()
    await session.execute(delete(AccountSession).where(AccountSession.user_id.in_(user_ids)))
    if device_ids:
        await session.execute(delete(DeviceSession).where(DeviceSession.id.in_(device_ids)))
    await session.execute(delete(User).where(User.id.in_(user_ids)))
    await session.execute(delete(Organization).where(Organization.id == race.env.org.id))
    await session.execute(delete(App).where(App.key == "calechip"))
    await session.commit()


@pytest.fixture
async def race() -> AsyncIterator[Race]:
    """Committed data (each request gets its own connection, so the accepts really race)."""
    from app.modules.invitations.models import OrgInvitation

    async with SessionLocal() as session:
        env = await setup_calechip(session)
        invitee = await person(session, None, email=INVITEE)
        token = "race-token-" + uuid.uuid4().hex
        invited_by = env.inviter.user.id
        session.add(
            OrgInvitation(
                org_id=env.org.id,
                app_id=env.app.id,
                role_id=env.viewer.id,
                email_hmac=_hmac(INVITEE),
                token_hash=token_hash(token),
                invited_by=invited_by,
                expires_at=(await session.scalar(text("SELECT now() + interval '1 day'"))),
            )
        )
        await session.commit()
    race_ = Race(create_app(), env, [invitee], token)
    try:
        yield race_
    finally:
        async with SessionLocal() as session:
            await _cleanup(session, race_)


def _hmac(email: str) -> bytes:
    from app.modules.auth.throttle import EmailHasher
    from tests.auth_factories import HMAC_KEY

    return EmailHasher(HMAC_KEY).hmac(email)


async def test_concurrent_accepts_one_succeeds(race: Race) -> None:
    invitee = race.people[0]

    async def accept() -> Response:
        transport = ASGITransport(app=race.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                ACCEPT_URL,
                json={"account_id": str(invitee.account.id), "token": race.token},
                headers={"X-MM-App": "calechip", "Origin": ORIGIN, "Cookie": _cookie(invitee)},
            )

    responses = await asyncio.wait_for(asyncio.gather(accept(), accept()), timeout=20)
    assert sorted(r.status_code for r in responses) == [200, 422]
    loser = next(r for r in responses if r.status_code == 422)
    assert _error(loser)["code"] == "token_invalid"
    async with SessionLocal() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM role_assignments WHERE user_id = :u AND org_id = :o"),
            {"u": invitee.user.id, "o": race.env.org.id},
        )
    assert count == 1


def _cookie(who: Person) -> str:
    return auth_headers(cookie=who.raw)["Cookie"]
