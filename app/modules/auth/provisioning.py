"""Operator-vouched account creation (USR-09, ADR-0010). CLI only: never import from a router."""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.errors import EmailTakenError, WeakPasswordError
from app.modules.auth.passwords import PasswordHasher, PasswordPolicy
from app.modules.auth.schemas import PASSWORD_MAX_LENGTH
from app.modules.membership.service import join_default_role, lock_joinable_app
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import AssignmentService
from app.modules.users.models import User
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)


async def vet_and_hash_password(
    password: str, *, policy: PasswordPolicy, hasher: PasswordHasher
) -> str:
    """Same rules as sign-up (policy + 128-character cap), then Argon2id. No DB work."""
    if len(password) > PASSWORD_MAX_LENGTH:
        raise WeakPasswordError(
            "Password does not meet the policy", details={"reasons": ["too_long"]}
        )
    policy.check(password)
    return await hasher.hash(password)


@dataclass(frozen=True)
class ProvisionResult:
    user_id: uuid.UUID
    email: str
    joined: str | None


class AccountProvisioningService:
    """CLI only (USR-09, ADR-0010); never call from a router. The caller owns the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_verified(
        self, email: str, password_hash: str, join_key: str | None
    ) -> ProvisionResult:
        """`email` is already normalized. Every refusal raises, so nothing commits."""
        app = role = None
        if join_key is not None:  # lock order app -> users (ADR-0003 §10), before the insert
            rbac = RbacRepository(self.session)
            app = await lock_joinable_app(rbac, join_key)
            role = await join_default_role(rbac, app)
        try:
            user = await UserRepository(self.session).add(
                User(email=email, password_hash=password_hash, email_verified_at=func.now())
            )
        except IntegrityError:
            raise EmailTakenError("Email is already registered") from None
        assignment_id = None
        if app is not None and role is not None:
            ra, _ = await AssignmentService(self.session)._grant_locked(
                app, role, user.id, None, None, None
            )
            assignment_id = ra.id
        # R4: ADM-004 audit entry (actor system:cli), same transaction: user.create_verified
        # (+ app.join).
        logger.info(
            "account_created actor=system:cli user_id=%s app_id=%s role_id=%s assignment_id=%s",
            user.id,
            app.id if app else "-",
            role.id if role else "-",
            assignment_id or "-",
        )
        return ProvisionResult(user.id, user.email, join_key)
