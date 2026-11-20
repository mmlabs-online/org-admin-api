"""Deployer credential auth — R3 class 4 (b) (ADR-0021 §3).

Format: `Authorization: Bearer mmdk_<key_id>_<secret>`.

Only the release deployment report route (ADM-036) may use it, and its per-IP rate-limit
dependency must come first. No production route uses it yet.
"""

from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import SessionDep
from app.modules.deployer_credentials.service import (
    AuthenticatedDeployer,
    DeployerCredentialService,
)

deployer_credential_scheme = HTTPBearer(
    scheme_name="deployerCredential", bearerFormat="mmdk", auto_error=False
)


async def current_deployer(
    session: SessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(deployer_credential_scheme)
    ],
) -> AuthenticatedDeployer:
    raw = None if credentials is None else credentials.credentials
    return await DeployerCredentialService(session).authenticate(raw)


DeployerDep = Annotated[AuthenticatedDeployer, Depends(current_deployer)]
