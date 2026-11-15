"""Access-token verification (ADR-0005, ADR-0008): the checks `docs/consumers.md` prescribes.

Pure (no ORM, no FastAPI), so `mmlabs-auth` can copy it next to `permits()`. Every failure
raises `InvalidAccessTokenError` with a fixed reason code; the token never appears in it (R11).
"""

from typing import Any

import jwt

from app.core.jwt_keys import ALGORITHM, TOKEN_TYP, KeySet

REQUIRED_CLAIMS = ["exp", "iat", "sub", "jti", "iss", "aud"]


class InvalidAccessTokenError(Exception):
    """`reason` is one of: malformed, typ, alg, kid, signature, expired, audience, issuer,
    claims, email_verified. For logs only, never for responses."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __repr__(self) -> str:
        return f"InvalidAccessTokenError({self.reason!r})"


def verify_access_token(
    token: str, key_set: KeySet, *, audience: str, issuer: str, leeway: int = 30
) -> dict[str, Any]:
    """The verified claims, or `InvalidAccessTokenError`."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        raise InvalidAccessTokenError("malformed") from None
    if header.get("typ") != TOKEN_TYP:
        raise InvalidAccessTokenError("typ")
    if header.get("alg") != ALGORITHM:
        raise InvalidAccessTokenError("alg")
    kid = header.get("kid")
    if not isinstance(kid, str) or kid not in key_set.public_keys:
        raise InvalidAccessTokenError("kid")
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key_set.public_keys[kid],
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=issuer,
            leeway=leeway,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError:
        raise InvalidAccessTokenError("expired") from None
    except jwt.InvalidSignatureError:
        raise InvalidAccessTokenError("signature") from None
    except jwt.InvalidAudienceError:
        raise InvalidAccessTokenError("audience") from None
    except jwt.InvalidIssuerError:
        raise InvalidAccessTokenError("issuer") from None
    except jwt.DecodeError:
        raise InvalidAccessTokenError("malformed") from None
    except jwt.InvalidTokenError:  # missing claim, future `iat`, bad claim types
        raise InvalidAccessTokenError("claims") from None
    if claims.get("email_verified") is not True:
        raise InvalidAccessTokenError("email_verified")
    return claims
