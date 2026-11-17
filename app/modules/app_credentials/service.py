"""Staff-side management of app credentials (USR-05a, ADR-0016). HTTP-free (R1).

Every mutator takes the app lock first (the same lock as every RBAC writer), which serialises
the "at most 2 active" limit. The raw credential leaves this module once, in `IssuedCredential`;
it is never logged, audited or stored (R11).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DENIED_ERRORS, ConflictError, NotFoundError
from app.modules.app_credentials.codec import new_credential, parse_credential, secret_hash
from app.modules.app_credentials.models import AppCredential
from app.modules.app_credentials.repository import AppCredentialRepository
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.rbac.actor import Actor, actor_user_id, require_super
from app.modules.rbac.models import ADMIN_APP_KEY, App, AppStatus
from app.modules.rbac.repository import RbacRepository

MAX_ACTIVE = 2  # rotation overlap (ADR-0016 §3)


@dataclass(frozen=True)
class CredentialView:
    """Plain values for the router, so it never touches the ORM."""

    id: uuid.UUID
    app: str
    key_id: str
    created_by: uuid.UUID | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    active: bool


@dataclass(frozen=True)
class IssuedCredential(CredentialView):
    credential: str


def _view(app_key: str, row: AppCredential) -> CredentialView:
    return CredentialView(
        id=row.id,
        app=app_key,
        key_id=row.key_id,
        created_by=row.created_by,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        active=row.revoked_at is None,
    )


def _refusal(message: str, reason: str) -> ConflictError:
    return ConflictError(message, details=[{"field": "key", "reason": reason}])


class AppCredentialService:
    def __init__(self, session: AsyncSession) -> None:
        self.rbac = RbacRepository(session)
        self.repo = AppCredentialRepository(session)
        self.audit = AuditLogger(session)

    async def _locked_app(self, key: str) -> App:
        app = await self.rbac.lock_app(key)
        if app is None:
            raise NotFoundError("App not found")
        return app

    async def issue(self, key: str, *, actor: Actor, ctx: AuditContext) -> IssuedCredential:
        app = await self._locked_app(key)
        try:
            require_super(actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.APP_CREDENTIAL_ISSUED, exc=exc, actor=actor, ctx=ctx, app_key=key
            )
            raise
        if app.key == ADMIN_APP_KEY:
            raise _refusal("The admin app has no credentials", "admin_app")
        if app.status == AppStatus.DISABLED.value:
            raise _refusal("The app is disabled", "app_disabled")
        if await self.repo.count_active(app.id) >= MAX_ACTIVE:
            raise _refusal(
                f"At most {MAX_ACTIVE} active credentials per app; revoke one first",
                "credential_limit",
            )
        key_id, raw = new_credential()
        parsed = parse_credential(raw)
        assert parsed is not None  # noqa: S101 (own output)
        row = AppCredential(
            app_id=app.id,
            key_id=key_id,
            secret_hash=secret_hash(parsed[1]),
            created_by=actor_user_id(actor),
        )
        await self.repo.add(row)
        await self.audit.record(
            AuditAction.APP_CREDENTIAL_ISSUED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.APP_CREDENTIAL, row.id),
            app_key=key,
            changes={"key_id": diff.change(None, key_id)},
        )
        return IssuedCredential(**_view(key, row).__dict__, credential=raw)

    async def list(self, key: str, *, limit: int, offset: int) -> tuple[list[CredentialView], int]:
        app = await self.rbac.get_app_by_key(key)
        if app is None:
            raise NotFoundError("App not found")
        rows, total = await self.repo.page(app.id, limit=limit, offset=offset)
        return [_view(key, r) for r in rows], total

    async def revoke(
        self, key: str, credential_id: uuid.UUID, *, actor: Actor, ctx: AuditContext
    ) -> None:
        """Needs no `admin:*`: revoking only removes access. Revoking twice is a no-op."""
        app = await self._locked_app(key)
        row = await self.repo.get(app.id, credential_id)
        if row is None:
            raise NotFoundError("Credential not found")
        if row.revoked_at is not None:
            return
        revoked_at = await self.repo.revoke(row.id)
        if revoked_at is None:  # revoked concurrently; the lock makes this unreachable
            return
        await self.audit.record(
            AuditAction.APP_CREDENTIAL_REVOKED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.APP_CREDENTIAL, row.id),
            app_key=key,
            changes={"revoked_at": diff.change(None, revoked_at)},
        )
