"""Staff-side management of app credentials (USR-05a, ADR-0016). HTTP-free (R1).

Every mutator takes the app lock first (the same lock as every RBAC writer), which serialises
the "at most 2 active" limit. The raw credential leaves this module once, in `IssuedCredential`;
it is never logged, audited or stored (R11).
"""

import hmac
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DENIED_ERRORS, ConflictError, NotFoundError, UnauthorizedError
from app.modules.app_credentials.codec import new_credential, parse_credential, secret_hash
from app.modules.app_credentials.errors import (
    AppCredentialAppDisabledError,
    AppCredentialMismatchError,
    AppCredentialScopeError,
)
from app.modules.app_credentials.models import RUNTIME_SCOPES, AppCredential, AppCredentialScope
from app.modules.app_credentials.repository import AppCredentialRepository
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditOutcome, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.rbac.actor import Actor, SystemActor, actor_user_id, require_super
from app.modules.rbac.models import ADMIN_APP_KEY, App, AppStatus
from app.modules.rbac.repository import RbacRepository

logger = logging.getLogger(__name__)

MAX_ACTIVE = 2  # rotation overlap (ADR-0016 §3)
RUNTIME_TOUCH_INTERVAL_SECONDS = 60  # ADR-0022 §3
_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="app"'}


def _reject(reason: str, key_id: str | None = None) -> UnauthorizedError:
    """One answer for every credential failure (ADR-0016 §4); never logs the raw value (R11)."""
    logger.info("app_credential_denied reason=%s key_id=%s", reason, key_id or "-")
    return UnauthorizedError("Authentication required", headers=dict(_CHALLENGE))


@dataclass(frozen=True)
class AuthenticatedApp:
    """The app a valid credential belongs to; acts as a non-privileged system actor."""

    credential_id: uuid.UUID
    key_id: str
    app_id: uuid.UUID
    app_key: str
    scope: str

    @property
    def actor(self) -> SystemActor:
        return SystemActor(f"app:{self.app_key}/{self.key_id}", privileged=False)


@dataclass(frozen=True)
class CredentialView:
    """Plain values for the router, so it never touches the ORM."""

    id: uuid.UUID
    app: str
    key_id: str
    scope: str
    created_by: uuid.UUID | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    active: bool


@dataclass(frozen=True)
class IssuedCredential(CredentialView):
    credential: str = field(repr=False)


def _view(app_key: str, row: AppCredential) -> CredentialView:
    return CredentialView(
        id=row.id,
        app=app_key,
        key_id=row.key_id,
        scope=row.scope,
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

    async def issue(
        self, key: str, *, scope: AppCredentialScope, actor: Actor, ctx: AuditContext
    ) -> IssuedCredential:
        app = await self._locked_app(key)
        try:
            require_super(actor)
        except DENIED_ERRORS as exc:
            await self.audit.record_denied(
                AuditAction.APP_CREDENTIAL_ISSUED, exc=exc, actor=actor, ctx=ctx, app_key=key
            )
            raise
        # Allow-list: only `releases` keys may be issued for `admin` (ADR-0021 §11, ADR-0022 §1).
        if app.key == ADMIN_APP_KEY and scope != AppCredentialScope.RELEASES:
            raise _refusal("The admin app has no credentials", "admin_app")
        if app.status == AppStatus.DISABLED.value:
            raise _refusal("The app is disabled", "app_disabled")
        if await self.repo.count_active(app.id, scope.value) >= MAX_ACTIVE:
            raise _refusal(
                f"At most {MAX_ACTIVE} active credentials per app and scope; revoke one first",
                "credential_limit",
            )
        key_id, raw = new_credential()
        parsed = parse_credential(raw)
        assert parsed is not None  # noqa: S101 (own output)
        row = AppCredential(
            app_id=app.id,
            key_id=key_id,
            secret_hash=secret_hash(parsed[1]),
            scope=scope.value,
            created_by=actor_user_id(actor),
        )
        await self.repo.add(row)
        await self.audit.record(
            AuditAction.APP_CREDENTIAL_ISSUED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.APP_CREDENTIAL, row.id),
            app_key=key,
            changes={"key_id": diff.change(None, key_id), "scope": diff.change(None, scope)},
        )
        return IssuedCredential(**_view(key, row).__dict__, credential=raw)

    async def authenticate(
        self,
        raw: str | None,
        path_key: str,
        *,
        scope: AppCredentialScope,
        denied_action: AuditAction,
        ctx: AuditContext,
        lock: bool = True,
    ) -> AuthenticatedApp:
        """Checks run in a fixed order; every credential failure is one 401 (ADR-0016 §4).

        Order: 401s, disabled app, other app, wrong scope, then (when `lock`) `confirm`.
        """
        if raw is None:
            raise _reject("missing")
        parsed = parse_credential(raw)
        if parsed is None:
            raise _reject("malformed")
        key_id, secret = parsed
        found = await self.repo.get_with_app(key_id)
        if found is None:
            raise _reject("unknown", key_id)
        row, app = found
        if not hmac.compare_digest(row.secret_hash, secret_hash(secret)):
            raise _reject("secret", key_id)
        if row.revoked_at is not None:
            raise _reject("revoked", key_id)
        if app.status != AppStatus.ACTIVE.value:
            logger.info("app_credential_denied reason=disabled key_id=%s", key_id)
            raise AppCredentialAppDisabledError("The app is disabled")
        caller = AuthenticatedApp(row.id, key_id, app.id, app.key, row.scope)
        if app.key != path_key:
            logger.info("app_credential_denied reason=mismatch key_id=%s", key_id)
            await self._denied(denied_action, caller, ctx, AppCredentialMismatchError.code)
            raise AppCredentialMismatchError("Credential belongs to another app")
        if row.scope != scope.value:
            logger.info("app_credential_denied reason=scope key_id=%s", key_id)
            await self._denied(denied_action, caller, ctx, AppCredentialScopeError.code)
            raise AppCredentialScopeError("Credential has the wrong scope for this route")
        if scope in RUNTIME_SCOPES:  # no app lock; a revoke in flight may complete (ADR-0022 §3)
            await self.repo.touch_last_used_throttled(
                row.id, interval_seconds=RUNTIME_TOUCH_INTERVAL_SECONDS
            )
            return caller
        if lock:
            await self.confirm(caller)
        return caller

    async def _denied(
        self, action: AuditAction, caller: AuthenticatedApp, ctx: AuditContext, reason: str
    ) -> None:
        await self.audit.record_failure(
            action,
            outcome=AuditOutcome.DENIED,
            actor=caller.actor,
            ctx=ctx,
            app_key=caller.app_key,
            changes={"reason": reason},
        )

    async def confirm(self, caller: AuthenticatedApp) -> None:
        """App lock, status re-check, then touch (ADR-0021 §1: lock before `last_used_at`).

        Same order as revoke (app lock, then credential row); the touch re-checks revocation.
        """
        app = await self.rbac.lock_app(caller.app_key)
        if app is None or app.status != AppStatus.ACTIVE.value:
            logger.info("app_credential_denied reason=disabled key_id=%s", caller.key_id)
            raise AppCredentialAppDisabledError("The app is disabled")
        if not await self.repo.touch_last_used(caller.credential_id):
            raise _reject("revoked", caller.key_id)

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
