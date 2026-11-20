"""App credential auth, R3 class 4 (ADR-0016, ADR-0021): `Authorization: Bearer mmak_...`.

One dependency per route, bound to the scope and the denied audit action of that route. The rate
limit is a route dependency, so it runs first. `lock=False` defers the app lock and the
`last_used_at` touch to `AppCredentialService.confirm`, which the route's service calls after the
request body was read (so a slow client never holds the app lock).
"""

from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import SessionDep
from app.modules.app_credentials.models import AppCredentialScope
from app.modules.app_credentials.service import AppCredentialService, AuthenticatedApp
from app.modules.audit.actions import AuditAction
from app.modules.audit.deps import AuditContextDep

app_credential_scheme = HTTPBearer(
    scheme_name="appCredential", bearerFormat="mmak", auto_error=False
)


class RequireAppCredential:
    def __init__(
        self, scope: AppCredentialScope, denied_action: AuditAction, *, lock: bool = True
    ) -> None:
        self.scope = scope
        self.denied_action = denied_action
        self.lock = lock

    async def __call__(
        self,
        key: str,
        session: SessionDep,
        ctx: AuditContextDep,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(app_credential_scheme)
        ],
    ) -> AuthenticatedApp:
        raw = None if credentials is None else credentials.credentials
        return await AppCredentialService(session).authenticate(
            raw,
            key,
            scope=self.scope,
            denied_action=self.denied_action,
            ctx=ctx,
            lock=self.lock,
        )


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
