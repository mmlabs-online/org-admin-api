"""Access-token signing keys: ES256 / P-256, `kid` = RFC 7638 thumbprint (ADR-0005).

Pure (no ORM). Keys arrive as base64 of a PEM, on one line (`JWT_SIGNING_KEY`,
`JWT_PUBLIC_KEYS_EXTRA`). Parse errors carry a fixed message and never the input (R5, R11).
"""

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)

from app.core.etag import strong_etag

ALGORITHM = "ES256"
TOKEN_TYP = "at+jwt"  # noqa: S105 - JWT header type, not a secret
_COORD_BYTES = 32  # P-256


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode_pem(value: str) -> bytes:
    return base64.b64decode(value.strip(), validate=True)


def parse_private_key_b64(value: str) -> ec.EllipticCurvePrivateKey:
    """A P-256 private key (PKCS#8 or SEC1 PEM, base64 on one line)."""
    try:
        key = load_pem_private_key(_decode_pem(value), password=None)
    except (ValueError, TypeError, binascii.Error):
        raise ValueError("not a valid base64 PEM P-256 private key") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("not a valid base64 PEM P-256 private key")
    return key


def parse_public_key_b64(value: str) -> ec.EllipticCurvePublicKey:
    """A P-256 public key (SPKI PEM, base64 on one line)."""
    try:
        key = load_pem_public_key(_decode_pem(value))
    except (ValueError, TypeError, binascii.Error):
        raise ValueError("not a valid base64 PEM P-256 public key") from None
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("not a valid base64 PEM P-256 public key")
    return key


def _coords(pub: ec.EllipticCurvePublicKey) -> tuple[str, str]:
    numbers = pub.public_numbers()
    return (
        _b64url(numbers.x.to_bytes(_COORD_BYTES, "big")),
        _b64url(numbers.y.to_bytes(_COORD_BYTES, "big")),
    )


def thumbprint(pub: ec.EllipticCurvePublicKey) -> str:
    """RFC 7638: SHA-256 over the required members, sorted, no whitespace; base64url."""
    x, y = _coords(pub)
    canonical = json.dumps(
        {"crv": "P-256", "kty": "EC", "x": x, "y": y}, separators=(",", ":"), sort_keys=True
    )
    return _b64url(hashlib.sha256(canonical.encode("ascii")).digest())


def public_jwk(pub: ec.EllipticCurvePublicKey, kid: str) -> dict[str, str]:
    """Public members only (never `d`)."""
    x, y = _coords(pub)
    return {"kty": "EC", "crv": "P-256", "x": x, "y": y, "kid": kid, "alg": ALGORITHM, "use": "sig"}


@dataclass(frozen=True)
class KeySet:
    signing_key: ec.EllipticCurvePrivateKey = field(repr=False)
    signing_kid: str
    extra_kids: tuple[str, ...]
    jwks_body: bytes = field(repr=False)
    jwks_etag: str

    def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(
            claims,
            self.signing_key,
            algorithm=ALGORITHM,
            headers={"kid": self.signing_kid, "typ": TOKEN_TYP},
        )


def build_key_set(signing_b64: str | None, extra_b64: list[str]) -> KeySet:
    """`signing_b64=None` => a fresh ephemeral key (local/test only; Settings enforce it)."""
    signing_key = (
        parse_private_key_b64(signing_b64)
        if signing_b64 is not None
        else ec.generate_private_key(ec.SECP256R1())
    )
    signing_kid = thumbprint(signing_key.public_key())
    keys = [public_jwk(signing_key.public_key(), signing_kid)]
    kids = [signing_kid]
    for value in extra_b64:
        pub = parse_public_key_b64(value)
        kid = thumbprint(pub)
        if kid in kids:
            raise ValueError("duplicate key in JWT_PUBLIC_KEYS_EXTRA")
        kids.append(kid)
        keys.append(public_jwk(pub, kid))
    body = json.dumps({"keys": keys}, separators=(",", ":"), sort_keys=True).encode()
    return KeySet(
        signing_key=signing_key,
        signing_kid=signing_kid,
        extra_kids=tuple(kids[1:]),
        jwks_body=body,
        jwks_etag=strong_etag(body),
    )
