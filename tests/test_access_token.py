"""Unit tests for the pure access-token verifier (ADM-002, ADR-0008)."""

import base64
import time
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.access_token import InvalidAccessTokenError, verify_access_token
from app.core.jwt_keys import KeySet, build_key_set, thumbprint
from tests.admin_auth_factories import DROP, KEY_SET, claims_for, mint, mint_raw
from tests.conftest import TEST_JWT_ISSUER, TEST_JWT_SIGNING_KEY

SUB = "3f1c2b8e-0000-4000-8000-000000000001"
HEADERS = {"kid": KEY_SET.signing_kid, "typ": "at+jwt"}


def _verify(token: str, key_set: KeySet = KEY_SET) -> dict[str, Any]:
    return verify_access_token(token, key_set, audience="admin", issuer=TEST_JWT_ISSUER)


def _reason(token: str, key_set: KeySet = KEY_SET) -> str:
    with pytest.raises(InvalidAccessTokenError) as exc_info:
        _verify(token, key_set)
    return exc_info.value.reason


def test_valid_token_returns_claims() -> None:
    claims = _verify(mint(SUB))
    assert claims["sub"] == SUB
    assert claims["aud"] == "admin"


def test_extra_key_token_verifies() -> None:
    rotation = ec.generate_private_key(ec.SECP256R1())
    pem = rotation.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_set = build_key_set(TEST_JWT_SIGNING_KEY, [base64.b64encode(pem).decode()])
    kid = thumbprint(rotation.public_key())
    token = mint_raw({"kid": kid, "typ": "at+jwt"}, claims_for(SUB), rotation, "ES256")
    assert _verify(token, key_set)["sub"] == SUB
    assert _reason(token) == "kid"  # not published in the default key set


def test_expired_rejected() -> None:
    now = int(time.time())
    assert _reason(mint(SUB, iat=now - 1000, exp=now - 31)) == "expired"


def test_long_lived_token_accepted() -> None:
    now = int(time.time())
    assert _verify(mint(SUB, iat=now - 43000, exp=now + 200))["sub"] == SUB


def test_expiry_within_leeway_accepted() -> None:
    now = int(time.time())
    assert _verify(mint(SUB, iat=now - 1000, exp=now - 10))["sub"] == SUB


def test_future_iat_rejected() -> None:
    now = int(time.time())
    assert _reason(mint(SUB, iat=now + 120, exp=now + 900)) == "claims"


@pytest.mark.parametrize("aud", ["stoktok", ["stoktok"]])
def test_wrong_audience_rejected(aud: object) -> None:
    assert _reason(mint(SUB, aud=aud)) == "audience"


@pytest.mark.parametrize("aud", [["admin"], ["stoktok", "admin"]])
def test_list_audience_rejected(aud: list[str]) -> None:
    # `strict_aud`: a list `aud` fails even when it contains "admin" (ADM-002d).
    assert _reason(mint(SUB, aud=aud)) == "audience"


def test_wrong_issuer_rejected() -> None:
    assert _reason(mint(SUB, iss="https://evil.invalid")) == "issuer"


def test_unknown_kid_rejected() -> None:
    token = mint_raw(
        {"kid": "unknown", "typ": "at+jwt"}, claims_for(SUB), KEY_SET.signing_key, "ES256"
    )
    assert _reason(token) == "kid"


def test_missing_kid_rejected() -> None:
    token = mint_raw({"typ": "at+jwt"}, claims_for(SUB), KEY_SET.signing_key, "ES256")
    assert _reason(token) == "kid"


def test_alg_none_rejected() -> None:
    assert _reason(mint_raw(HEADERS, claims_for(SUB), None, "none")) == "alg"


def test_hs256_rejected() -> None:
    public_pem = KEY_SET.public_keys[KEY_SET.signing_kid].public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert _reason(mint_raw(HEADERS, claims_for(SUB), public_pem, "HS256")) == "alg"


@pytest.mark.parametrize("typ", ["JWT", DROP], ids=["JWT", "missing"])
def test_wrong_typ_rejected(typ: object) -> None:
    token = mint_raw(
        {"kid": KEY_SET.signing_kid, "typ": typ}, claims_for(SUB), KEY_SET.signing_key, "ES256"
    )
    assert _reason(token) == "typ"


@pytest.mark.parametrize("claim", ["exp", "iat", "sub", "jti", "iss", "aud"])
def test_missing_claim_rejected(claim: str) -> None:
    assert _reason(mint(SUB, **{claim: DROP})) == "claims"


@pytest.mark.parametrize("value", [False, DROP, "true"], ids=["false", "missing", "string"])
def test_email_verified_must_be_true(value: object) -> None:
    assert _reason(mint(SUB, email_verified=value)) == "email_verified"


def test_wrong_signature_rejected() -> None:
    other = ec.generate_private_key(ec.SECP256R1())
    assert _reason(mint_raw(HEADERS, claims_for(SUB), other, "ES256")) == "signature"


@pytest.mark.parametrize("token", ["abc", "a.b.c", ""])
def test_garbage_rejected(token: str) -> None:
    assert _reason(token) == "malformed"


def test_error_carries_no_token() -> None:
    token = mint(SUB, aud="stoktok")
    with pytest.raises(InvalidAccessTokenError) as exc_info:
        _verify(token)
    assert str(exc_info.value) == "audience"
    assert repr(exc_info.value) == "InvalidAccessTokenError('audience')"
    for part in token.split("."):
        assert part not in str(exc_info.value)
        assert part not in repr(exc_info.value)
