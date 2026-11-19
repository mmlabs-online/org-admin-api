"""Shared setup for the org invitation tests (USR-06b): app `calechip`, roles, an org, people."""

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.email.sender import OutgoingEmail
from app.modules.invitations.models import OrgInvitation
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App, Permission, Role
from app.modules.sessions.models import AccountSession
from app.modules.users.models import User
from tests.auth_factories import auth_headers, make_device, make_login_app, make_login_user
from tests.rbac_factories import assign, grant, make_org, make_permission, make_role

CREATE_URL = "/api/v1/auth/orgs/{org_id}/invitations"
ACCEPT_URL = "/api/v1/auth/invitations/accept"


@dataclass
class Person:
    user: User
    raw: str
    account: AccountSession

    def headers(self, app_key: str = "calechip") -> dict[str, str]:
        return auth_headers(app_key=app_key, cookie=self.raw)


@dataclass
class Env:
    app: App
    org: Organization
    member: Role  # default role: `org:create` only
    owner: Role  # org owner role: invite, edit, read
    viewer: Role  # invitable: `data:read`
    perms: dict[str, Permission]
    inviter: Person  # app member + owner in `org`


async def person(
    db_session: AsyncSession, role: Role | None, *, org: Organization | None = None, **kw: Any
) -> Person:
    """A signed-in user; `role` is assigned in `org` when given, else at app scope."""
    user = await make_login_user(db_session, **kw)
    if role is not None:
        await assign(db_session, user, role, org_id=None if org is None else org.id)
    raw, _, (account,) = await make_device(db_session, user)
    return Person(user, raw, account)


async def setup_calechip(db_session: AsyncSession) -> Env:
    app = await make_login_app(db_session, "calechip", org_mode="required")
    keys = ["org:create", "org:invite", "org:edit", "data:read"]
    perms = {
        k: await make_permission(
            db_session, app, f"calechip:{k}", scope="app" if k == "org:create" else "org"
        )
        for k in keys
    }
    member = await make_role(db_session, app, "calechip:member")
    await grant(db_session, member, perms["org:create"])
    owner = await make_role(db_session, app, "calechip:org-owner")
    await grant(db_session, owner, perms["org:invite"], perms["org:edit"], perms["data:read"])
    viewer = await make_role(db_session, app, "calechip:org-viewer")
    await grant(db_session, viewer, perms["data:read"])
    app.default_role_id = member.id
    app.org_owner_role_id = owner.id
    await db_session.flush()
    org = await make_org(db_session, name="Acme")
    inviter = await person(db_session, member)
    await assign(db_session, inviter.user, owner, org_id=org.id)
    return Env(app, org, member, owner, viewer, perms, inviter)


def create_body(who: Person, email: str, role: str = "calechip:org-viewer") -> dict[str, str]:
    return {"account_id": str(who.account.id), "email": email, "role": role}


def link_token(email: OutgoingEmail) -> str:
    match = re.search(r"#token=(\S+)", email.body)
    assert match is not None
    return match.group(1)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


async def invitations(db_session: AsyncSession) -> list[OrgInvitation]:
    return list(await db_session.scalars(select(OrgInvitation).order_by(OrgInvitation.created_at)))


async def make_invitation(
    db_session: AsyncSession,
    org: Organization,
    role: Role,
    *,
    created_at: datetime,
    expires_at: datetime,
    accepted_by: User | None = None,
    revoked_at: datetime | None = None,
    invited_by: User | None = None,
) -> tuple[OrgInvitation, str]:
    """Insert an invitation directly (random hashes); returns it and its raw token.
    `accepted_by` set means accepted (at `created_at`); `revoked_at` means revoked."""
    raw = os.urandom(16).hex()
    invitation = OrgInvitation(
        org_id=org.id,
        app_id=role.app_id,
        role_id=role.id,
        email_hmac=os.urandom(32),
        token_hash=hashlib.sha256(raw.encode()).digest(),
        invited_by=invited_by.id if invited_by else None,
        created_at=created_at,
        expires_at=expires_at,
        accepted_at=created_at if accepted_by else None,
        accepted_by=accepted_by.id if accepted_by else None,
        revoked_at=revoked_at,
    )
    db_session.add(invitation)
    await db_session.flush()
    return invitation, raw
