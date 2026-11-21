"""Shared setup for the org member removal tests (USR-30): builds on the invitation setup."""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.organizations.models import Organization
from app.modules.rbac.models import Permission, Role
from tests.invitation_factories import Env, Person, person, setup_calechip
from tests.rbac_factories import grant, make_permission, make_role

REMOVE_URL = "/api/v1/auth/orgs/{org_id}/members/{user_id}/remove"


@dataclass
class RemoveEnv:
    env: Env
    remove_perm: Permission
    manager: Role  # `calechip:org-manager`: remove-member + data:read (a subset of the owner)

    @property
    def org(self) -> Organization:
        return self.env.org

    @property
    def owner(self) -> Person:
        return self.env.inviter


async def setup_removal(db_session: AsyncSession) -> RemoveEnv:
    env = await setup_calechip(db_session)
    remove = await make_permission(db_session, env.app, "calechip:org:remove-member", scope="org")
    await grant(db_session, env.owner, remove)
    manager = await make_role(db_session, env.app, "calechip:org-manager")
    await grant(db_session, manager, remove, env.perms["data:read"])
    return RemoveEnv(env, remove, manager)


def remove_url(org_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return REMOVE_URL.format(org_id=org_id, user_id=user_id)


def remove_body(caller: Person) -> dict[str, str]:
    return {"account_id": str(caller.account.id)}


async def member(db_session: AsyncSession, renv: RemoveEnv, role: Role, **kw: object) -> Person:
    """A signed-in user holding `role` in the env's org."""
    return await person(db_session, role, org=renv.org, **kw)
