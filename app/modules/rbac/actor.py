"""Who performs an RBAC or user-status write (ADM-002b, ADR-0008 amendment). HTTP-free (R1).

Every mutator takes a required `actor`: a staff principal from the router, or an explicit system
actor (CLI, join), so nothing fails open by forgetting it.
"""

import uuid
from dataclasses import dataclass

from app.core.errors import AdminWildcardRequiredError
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.rbac.authz import permits


@dataclass(frozen=True)
class SystemActor:
    name: str
    privileged: bool


SYSTEM_CLI = SystemActor("system:cli", privileged=True)
SYSTEM_JOIN = SystemActor("system:join", privileged=False)

Actor = AdminPrincipal | SystemActor


def is_super(actor: Actor) -> bool:
    if isinstance(actor, SystemActor):
        return actor.privileged
    return permits(actor.permissions, "admin:*")


def require_super(actor: Actor) -> None:
    if not is_super(actor):
        raise AdminWildcardRequiredError()


def actor_user_id(actor: Actor) -> uuid.UUID | None:
    return None if isinstance(actor, SystemActor) else actor.user_id
