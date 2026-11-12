"""The reference verifier (mirrors `docs/consumers.md`) rejects every forged or wrong token."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient

from app.core.jwt_keys import KeySet, build_key_set
from tests.conftest import TEST_JWT_ISSUER, TEST_JWT_SIGNING_KEY
from tests.jwt_verifier import verify_reference

JWKS = "/.well-known/jwks.json"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": TEST_JWT_ISSUER,
        "aud": "stoktok",
        "sub": str(uuid.uuid4()),
        "email": "a@example.com",
        "email_verified": True,
        "roles": ["stoktok:viewer"],
        "iat": now,
        "exp": now + 900,
        "jti": str(uuid.uuid4()),
    }
    claims.update(overrides)
    return claims


@pytest.fixture
async def jwks(client: AsyncClient) -> dict[str, Any]:
    body: dict[str, Any] = (await client.get(JWKS)).json()
    return body


@pytest.fixture
def signer() -> KeySet:
    """The same key the app serves (`JWT_SIGNING_KEY` from conftest)."""
    return build_key_set(TEST_JWT_SIGNING_KEY, [])


def _verify(token: str, jwks: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return verify_reference(
        token,
        jwks,
        audience=kwargs.pop("audience", "stoktok"),
        issuer=kwargs.pop("issuer", TEST_JWT_ISSUER),
    )


def _forge(header: dict[str, Any], claims: dict[str, Any], signature: bytes = b"") -> str:
    head = _b64url(json.dumps(header).encode())
    body = _b64url(json.dumps(claims).encode())
    return f"{head}.{body}.{_b64url(signature)}"


def test_reference_accepts_valid(jwks: dict[str, Any], signer: KeySet) -> None:
    assert jwks["keys"][0]["kid"] == signer.signing_kid
    assert _verify(signer.sign(_claims()), jwks)["aud"] == "stoktok"


def test_reference_rejects_tampered_payload(jwks: dict[str, Any], signer: KeySet) -> None:
    head, _, sig = signer.sign(_claims()).split(".")
    forged = _b64url(json.dumps(_claims(roles=["stoktok:admin"])).encode())
    with pytest.raises(jwt.InvalidSignatureError):
        _verify(f"{head}.{forged}.{sig}", jwks)


def test_reference_rejects_other_key_same_kid(jwks: dict[str, Any], signer: KeySet) -> None:
    other = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(
        _claims(), other, algorithm="ES256", headers={"kid": signer.signing_kid, "typ": "at+jwt"}
    )
    with pytest.raises(jwt.InvalidSignatureError):
        _verify(token, jwks)


def test_reference_rejects_alg_none(jwks: dict[str, Any], signer: KeySet) -> None:
    token = _forge({"alg": "none", "kid": signer.signing_kid, "typ": "at+jwt"}, _claims())
    with pytest.raises(jwt.InvalidTokenError):
        _verify(token, jwks)


def test_reference_rejects_hs256_key_confusion(jwks: dict[str, Any], signer: KeySet) -> None:
    public_pem = signer.signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    unsigned = _forge({"alg": "HS256", "kid": signer.signing_kid, "typ": "at+jwt"}, _claims())
    signing_input = unsigned.rsplit(".", 1)[0]
    mac = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    with pytest.raises(jwt.InvalidTokenError):
        _verify(f"{signing_input}.{_b64url(mac)}", jwks)


def test_reference_rejects_expired(jwks: dict[str, Any], signer: KeySet) -> None:
    now = int(time.time())
    token = signer.sign(_claims(iat=now - 1000, exp=now - 31))
    with pytest.raises(jwt.ExpiredSignatureError):
        _verify(token, jwks)


def test_reference_rejects_wrong_audience(jwks: dict[str, Any], signer: KeySet) -> None:
    with pytest.raises(jwt.InvalidAudienceError):
        _verify(signer.sign(_claims()), jwks, audience="ricotdin")


def test_reference_rejects_wrong_issuer(jwks: dict[str, Any], signer: KeySet) -> None:
    with pytest.raises(jwt.InvalidIssuerError):
        _verify(signer.sign(_claims()), jwks, issuer="https://api.test.invalid.evil")


def test_reference_rejects_unknown_kid(jwks: dict[str, Any]) -> None:
    stranger = build_key_set(None, [])
    with pytest.raises(jwt.InvalidTokenError, match="unknown kid"):
        _verify(stranger.sign(_claims()), jwks)


def test_reference_rejects_typ_jwt(jwks: dict[str, Any], signer: KeySet) -> None:
    token = jwt.encode(
        _claims(),
        signer.signing_key,
        algorithm="ES256",
        headers={"kid": signer.signing_kid, "typ": "JWT"},
    )
    with pytest.raises(jwt.InvalidTokenError, match="typ"):
        _verify(token, jwks)
