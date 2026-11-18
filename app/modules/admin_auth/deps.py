"""Admin endpoint auth — R3 class 1 (ADR-0008).

Bearer from the `Authorization` header only: never the device cookie, never a query parameter.
Routers add `dependencies=[Depends(current_admin)]`; every route adds
`**require_permission("admin:<resource>:<action>")`, which both declares (`x-permission`) and
enforces the permission.
"""

import logging
import re
import uuid
from typing import Annotated, Any

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import SessionDep
from app.core.access_token import InvalidAccessTokenError, verify_access_token
from app.core.config import Settings
from app.core.errors import PermissionDeniedError, UnauthorizedError
from app.core.jwt_keys import KeySet
from app.modules.admin_auth.service import (
    AdminAuthService,
    AdminPrincipal,
    AdminSessionEndedError,
)
from app.modules.rbac.authz import permits
from app.modules.rbac.models import ADMIN_APP_KEY

logger = logging.getLogger(__name__)

PERMISSION_KEY = "x-permission"
_PERMISSION_RE = re.compile(r"^admin:([a-z][a-z0-9-]{0,47}:[a-z][a-z0-9-]{0,47}|\*)$")
_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid Bearer token"},
    403: {"description": "Missing permission"},
}

bearer_scheme = HTTPBearer(scheme_name="bearerAuth", bearerFormat="JWT", auto_error=False)


def _unauthorized(*, token_sent: bool) -> UnauthorizedError:
    challenge = 'Bearer realm="admin"'
    if token_sent:
        challenge += ', error="invalid_token"'
    return UnauthorizedError("Authentication required", headers={"WWW-Authenticate": challenge})


def _deny(reason: str, jti: str | None = None, user_id: uuid.UUID | None = None) -> None:
    logger.info("admin_auth_denied reason=%s jti=%s user_id=%s", reason, jti or "-", user_id or "-")


async def current_admin(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> AdminPrincipal:
    if credentials is None:
        _deny("missing")
        raise _unauthorized(token_sent=False)
    key_set: KeySet = request.app.state.key_set
    settings: Settings = request.app.state.settings
    try:
        claims = verify_access_token(
            credentials.credentials,
            key_set,
            audience=ADMIN_APP_KEY,
            issuer=settings.jwt_issuer,
        )
    except InvalidAccessTokenError as exc:
        _deny(exc.reason)
        raise _unauthorized(token_sent=True) from None
    jti = str(claims["jti"])
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except ValueError:
        _deny("sub", jti)
        raise _unauthorized(token_sent=True) from None
    try:
        session_id = uuid.UUID(str(claims["sid"]))
    except (KeyError, ValueError):
        _deny("sid", jti, user_id)
        raise _unauthorized(token_sent=True) from None
    try:
        return await AdminAuthService(session).principal(user_id, jti, session_id=session_id)
    except AdminSessionEndedError:
        _deny("session", jti, user_id)
        raise _unauthorized(token_sent=True) from None
    except UnauthorizedError:
        _deny("user", jti, user_id)
        raise _unauthorized(token_sent=True) from None


AdminDep = Annotated[AdminPrincipal, Depends(current_admin)]


class RequirePermission:
    def __init__(self, key: str) -> None:
        self.key = key

    async def __call__(self, principal: AdminDep) -> None:
        if not permits(principal.permissions, self.key):
            logger.info(
                "admin_permission_denied user_id=%s jti=%s required=%s",
                principal.user_id,
                principal.token_id,
                self.key,
            )
            raise PermissionDeniedError("Missing permission", details={"required": self.key})


def require_permission(key: str) -> dict[str, Any]:
    """Route kwargs that declare and enforce `key`: `@router.get(..., **require_permission(k))`."""
    if not _PERMISSION_RE.fullmatch(key):
        raise ValueError(f"invalid admin permission key: {key!r}")
    return {
        "dependencies": [Depends(RequirePermission(key))],
        "openapi_extra": {PERMISSION_KEY: key},
        "responses": dict(_ERROR_RESPONSES),
    }
