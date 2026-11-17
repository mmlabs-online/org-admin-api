"""App credential auth — R3 class 4 (ADR-0016): `Authorization: Bearer mmak_<key_id>_<secret>`.

Only the manifest route uses it. The rate limit is a route dependency, so it runs first.
"""

from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import SessionDep
from app.modules.app_credentials.service import AppCredentialService, AuthenticatedApp
from app.modules.audit.deps import AuditContextDep

app_credential_scheme = HTTPBearer(
    scheme_name="appCredential", bearerFormat="mmak", auto_error=False
)


async def current_app_credential(
    key: str,
    session: SessionDep,
    ctx: AuditContextDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(app_credential_scheme)],
) -> AuthenticatedApp:
    raw = None if credentials is None else credentials.credentials
    return await AppCredentialService(session).authenticate(raw, key, ctx=ctx)


AppCredentialDep = Annotated[AuthenticatedApp, Depends(current_app_credential)]
