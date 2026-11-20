"""Staff-side management and authentication of deployer credentials (ADM-035, ADR-0021). HTTP-free.

Issue and revoke take one advisory lock, which serialises the "at most 2 active" limit. The raw
credential leaves this module once, in `IssuedDeployerCredential`; it is never logged, audited
or stored (R11). `authenticate` takes no lock: its guarded UPDATE sees a committed revoke at once.
"""

import hmac
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnauthorizedError
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.deployer_credentials.codec import new_credential, parse_credential, secret_hash
from app.modules.deployer_credentials.models import DeployerCredential
from app.modules.deployer_credentials.repository import DeployerCredentialRepository
from app.modules.rbac.actor import Actor, SystemActor, actor_user_id

logger = logging.getLogger(__name__)

MAX_ACTIVE = 2  # rotation overlap (ADR-0021 §2)
_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="deployer"'}


def _reject(reason: str, key_id: str | None = None) -> UnauthorizedError:
    """One answer for every credential failure (ADR-0021 §3); never logs the raw value (R11)."""
    logger.info("deployer_credential_denied reason=%s key_id=%s", reason, key_id or "-")
    return UnauthorizedError("Authentication required", headers=dict(_CHALLENGE))


@dataclass(frozen=True)
class AuthenticatedDeployer:
    """A valid deployer key; acts as a non-privileged system actor."""

    credential_id: uuid.UUID
    key_id: str

    @property
    def actor(self) -> SystemActor:
        return SystemActor(f"deployer:{self.key_id}", privileged=False)


@dataclass(frozen=True)
class DeployerCredentialView:
    """Plain values for the router, so it never touches the ORM."""

    id: uuid.UUID
    key_id: str
    created_by: uuid.UUID | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    active: bool


@dataclass(frozen=True)
class IssuedDeployerCredential(DeployerCredentialView):
    credential: str = field(repr=False)


def _view(row: DeployerCredential) -> DeployerCredentialView:
    return DeployerCredentialView(
        id=row.id,
        key_id=row.key_id,
        created_by=row.created_by,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        active=row.revoked_at is None,
    )


class DeployerCredentialService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = DeployerCredentialRepository(session)
        self.audit = AuditLogger(session)

    async def issue(self, *, actor: Actor, ctx: AuditContext) -> IssuedDeployerCredential:
        await self.repo.lock()
        if await self.repo.count_active() >= MAX_ACTIVE:
            raise ConflictError(
                f"At most {MAX_ACTIVE} active deployer credentials; revoke one first",
                details=[{"field": None, "reason": "credential_limit"}],
            )
        key_id, raw = new_credential()
        parsed = parse_credential(raw)
        assert parsed is not None  # noqa: S101 (own output)
        row = DeployerCredential(
            key_id=key_id,
            secret_hash=secret_hash(parsed[1]),
            created_by=actor_user_id(actor),
        )
        await self.repo.add(row)
        await self.audit.record(
            AuditAction.DEPLOYER_CREDENTIAL_ISSUED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.DEPLOYER_CREDENTIAL, row.id),
            changes={"key_id": diff.change(None, key_id)},
        )
        return IssuedDeployerCredential(**_view(row).__dict__, credential=raw)

    async def list(self, *, limit: int, offset: int) -> tuple[list[DeployerCredentialView], int]:
        rows, total = await self.repo.page(limit=limit, offset=offset)
        return [_view(r) for r in rows], total

    async def revoke(self, credential_id: uuid.UUID, *, actor: Actor, ctx: AuditContext) -> None:
        """Revoking twice is a no-op; revoking the last active key is allowed (incident path)."""
        await self.repo.lock()
        row = await self.repo.get(credential_id)
        if row is None:
            raise NotFoundError("Credential not found")
        if row.revoked_at is not None:
            return
        revoked_at = await self.repo.revoke(row.id)
        if revoked_at is None:  # revoked concurrently; the lock makes this unreachable
            return
        await self.audit.record(
            AuditAction.DEPLOYER_CREDENTIAL_REVOKED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.DEPLOYER_CREDENTIAL, row.id),
            changes={"revoked_at": diff.change(None, revoked_at)},
        )

    async def authenticate(self, raw: str | None) -> AuthenticatedDeployer:
        """Checks run in a fixed order; every failure is one 401 (ADR-0021 §3)."""
        if raw is None:
            raise _reject("missing")
        parsed = parse_credential(raw)
        if parsed is None:
            raise _reject("malformed")
        key_id, secret = parsed
        row = await self.repo.get_by_key_id(key_id)
        if row is None:
            raise _reject("unknown", key_id)
        if not hmac.compare_digest(row.secret_hash, secret_hash(secret)):
            raise _reject("secret", key_id)
        if row.revoked_at is not None:
            raise _reject("revoked", key_id)
        if not await self.repo.touch_last_used(row.id):
            raise _reject("revoked", key_id)
        return AuthenticatedDeployer(row.id, key_id)
