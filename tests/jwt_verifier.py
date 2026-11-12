"""Reference verifier: the checks `docs/consumers.md` prescribes for consuming apps."""

from typing import Any

import jwt
from jwt import PyJWKSet


def verify_reference(
    token: str, jwks: dict[str, Any], *, audience: str, issuer: str, leeway: int = 30
) -> dict[str, Any]:
    """Raise `jwt.InvalidTokenError` (or `AssertionError`) unless the token is acceptable."""
    header = jwt.get_unverified_header(token)
    if header.get("typ") != "at+jwt":
        raise jwt.InvalidTokenError("typ")
    kid = header.get("kid")
    key_set = PyJWKSet.from_dict(jwks)
    if not isinstance(kid, str) or kid not in {k.key_id for k in key_set.keys}:
        raise jwt.InvalidTokenError("unknown kid")
    claims: dict[str, Any] = jwt.decode(
        token,
        key_set[kid],
        algorithms=["ES256"],
        audience=audience,
        issuer=issuer,
        leeway=leeway,
        options={"require": ["exp", "iat", "sub", "jti", "iss", "aud"]},
    )
    if claims.get("email_verified") is not True:
        raise jwt.InvalidTokenError("email_verified")
    return claims
