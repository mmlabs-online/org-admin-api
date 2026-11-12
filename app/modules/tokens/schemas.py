import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.rbac.models import APP_KEY_RE


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience: str = Field(pattern=APP_KEY_RE)
    account_id: uuid.UUID
    org_id: uuid.UUID | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - OAuth token type name
    expires_in: int
    audience: str


class Jwk(BaseModel):
    """OpenAPI only: the public members of one signing key."""

    kty: Literal["EC"]
    crv: Literal["P-256"]
    x: str
    y: str
    kid: str
    alg: Literal["ES256"]
    use: Literal["sig"]


class JwkSet(BaseModel):
    """OpenAPI only."""

    keys: list[Jwk]
