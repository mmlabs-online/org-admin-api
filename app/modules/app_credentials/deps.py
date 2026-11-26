"""App credential auth, R3 class 4 (ADR-0016, ADR-0021): `Authorization: Bearer mmak_...`.

One dependency per route, bound to the scope and the denied audit action of that route. The rate
limit is a route dependency, so it runs first. `lock=False` defers the app lock and the
`last_used_at` touch to `AppCredentialService.confirm`, which the route's service calls after the
request body was read (so a slow client never holds the app lock). Runtime scopes (ADR-0025)
never lock: `authenticate` does a throttled `last_used_at` touch instead.
"""

from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import SessionDep
from app.core.errors import PermissionDeniedError, UnauthorizedError
from app.core.rate_limit import FailureScope, enforce_failure_limit
from app.modules.app_credentials.models import RUNTIME_SCOPES, AppCredentialScope
from app.modules.app_credentials.service import AppCredentialService, AuthenticatedApp
from app.modules.audit.actions import AuditAction
from app.modules.audit.deps import AuditContextDep

app_credential_scheme = HTTPBearer(
    scheme_name="appCredential", bearerFormat="mmak", auto_error=False
)


class RequireAppCredential:
    """One scope, or a set of scopes of which the key must hold one (all runtime scopes)."""

    def __init__(
        self,
        scope: AppCredentialScope | frozenset[AppCredentialScope],
        denied_action: AuditAction,
        *,
        lock: bool = True,
        failed_auth_scope: FailureScope | None = None,
    ) -> None:
        scopes = frozenset({scope}) if isinstance(scope, AppCredentialScope) else scope
        runtime = scopes & RUNTIME_SCOPES
        if runtime and (lock or runtime != scopes):
            raise ValueError("runtime scopes never take the app lock and are not mixed")
        self.scopes = scopes
        self.denied_action = denied_action
        self.lock = lock
        self.failed_auth_scope = failed_auth_scope

    async def __call__(
        self,
        request: Request,
        key: str,
        session: SessionDep,
        ctx: AuditContextDep,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(app_credential_scheme)
        ],
    ) -> AuthenticatedApp:
        raw = None if credentials is None else credentials.credentials
        try:
            return await AppCredentialService(session).authenticate(
                raw,
                key,
                scope=self.scopes,
                denied_action=self.denied_action,
                ctx=ctx,
                lock=self.lock,
            )
        except (UnauthorizedError, PermissionDeniedError):
            if self.failed_auth_scope is not None:
                enforce_failure_limit(request, self.failed_auth_scope)  # 429 replaces the error
            raise


AppCredentialDep = Annotated[
    AuthenticatedApp,
    Depends(RequireAppCredential(AppCredentialScope.MANIFEST, AuditAction.APP_MANIFEST_SYNCED)),
]
ReleasesCredentialDep = Annotated[
    AuthenticatedApp,
    Depends(
        RequireAppCredential(AppCredentialScope.RELEASES, AuditAction.RELEASE_CREATED, lock=False)
    ),
]
OrgMembersCredentialDep = Annotated[
    AuthenticatedApp,
    Depends(
        RequireAppCredential(
            AppCredentialScope.ORG_MEMBERS_READ,
            AuditAction.ORGANIZATION_MEMBERS_READ,
            lock=False,
        )
    ),
]
OrgInvitationsCredentialDep = Annotated[
    AuthenticatedApp,
    Depends(
        RequireAppCredential(
            AppCredentialScope.ORG_MEMBERS_READ,
            AuditAction.ORGANIZATION_INVITATIONS_READ,
            lock=False,
        )
    ),
]
UsageReadCredentialDep = Annotated[
    AuthenticatedApp,
    Depends(
        RequireAppCredential(
            frozenset({AppCredentialScope.USAGE_READ, AppCredentialScope.USAGE_WRITE}),
            AuditAction.USAGE_READ,
            lock=False,
            failed_auth_scope="usage_auth_failed",
        )
    ),
]
UsageConsumeCredentialDep = Annotated[
    AuthenticatedApp,
    Depends(
        RequireAppCredential(
            AppCredentialScope.USAGE_WRITE,
            AuditAction.USAGE_CONSUMED,
            lock=False,
            failed_auth_scope="usage_auth_failed",
        )
    ),
]
UsageSettleCredentialDep = Annotated[
    AuthenticatedApp,
    Depends(
        RequireAppCredential(
            AppCredentialScope.USAGE_WRITE,
            AuditAction.USAGE_SETTLED,
            lock=False,
            failed_auth_scope="usage_auth_failed",
        )
    ),
]
UsageRefundCredentialDep = Annotated[
    AuthenticatedApp,
    Depends(
        RequireAppCredential(
            AppCredentialScope.USAGE_WRITE,
            AuditAction.USAGE_REFUNDED,
            lock=False,
            failed_auth_scope="usage_auth_failed",
        )
    ),
]
