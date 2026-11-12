import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from app.core.jwt_keys import (
    build_key_set,
    parse_private_key_b64,
    parse_public_key_b64,
    thumbprint,
)


def _priv_b64(key: PrivateKeyTypes, fmt: serialization.PrivateFormat | None = None) -> str:
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        fmt or serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode()


def _pub_b64(key: ec.EllipticCurvePrivateKey) -> str:
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(pem).decode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _p256() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.mark.parametrize(
    "fmt", [serialization.PrivateFormat.PKCS8, serialization.PrivateFormat.TraditionalOpenSSL]
)
def test_parse_private_key_roundtrip_and_kid_is_rfc7638(
    fmt: serialization.PrivateFormat,
) -> None:
    key = _p256()
    parsed = parse_private_key_b64(_priv_b64(key, fmt))
    numbers = key.public_key().public_numbers()
    assert parsed.public_key().public_numbers() == numbers
    members = {
        "crv": "P-256",
        "kty": "EC",
        "x": _b64url(numbers.x.to_bytes(32, "big")),
        "y": _b64url(numbers.y.to_bytes(32, "big")),
    }
    expected = _b64url(
        hashlib.sha256(json.dumps(members, separators=(",", ":"), sort_keys=True).encode()).digest()
    )
    assert thumbprint(parsed.public_key()) == expected


_MARKER = base64.b64encode(b"-----BEGIN PRIVATE KEY-----\nS3cretMarker\n").decode()


@pytest.mark.parametrize(
    "value",
    [
        "not base64 at all!",
        _MARKER,
        _priv_b64(ec.generate_private_key(ec.SECP384R1())),
        _priv_b64(ed25519.Ed25519PrivateKey.generate()),
        _priv_b64(rsa.generate_private_key(public_exponent=65537, key_size=2048)),
    ],
    ids=["not-base64", "not-pem", "p384", "ed25519", "rsa"],
)
def test_parse_rejects_garbage_non_base64_and_wrong_curve(value: str) -> None:
    with pytest.raises(ValueError) as exc:
        parse_private_key_b64(value)
    assert str(exc.value) == "not a valid base64 PEM P-256 private key"
    assert value not in str(exc.value)
    assert exc.value.__cause__ is None


def test_public_key_in_signing_slot_rejected() -> None:
    with pytest.raises(ValueError, match="private key"):
        build_key_set(_pub_b64(_p256()), [])


def test_private_key_in_extra_rejected() -> None:
    with pytest.raises(ValueError, match="public key"):
        build_key_set(None, [_priv_b64(_p256())])


def test_parse_public_key_rejects_wrong_curve() -> None:
    other = ec.generate_private_key(ec.SECP384R1())
    pem = other.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with pytest.raises(ValueError, match="public key"):
        parse_public_key_b64(base64.b64encode(pem).decode())


def test_duplicate_kid_rejected() -> None:
    signer = _p256()
    with pytest.raises(ValueError, match="duplicate"):
        build_key_set(_priv_b64(signer), [_pub_b64(signer)])
    extra = _p256()
    with pytest.raises(ValueError, match="duplicate"):
        build_key_set(_priv_b64(signer), [_pub_b64(extra), _pub_b64(extra)])


def test_jwks_members_allowlist() -> None:
    signer, extra = _p256(), _p256()
    key_set = build_key_set(_priv_b64(signer), [_pub_b64(extra)])
    keys = json.loads(key_set.jwks_body)["keys"]
    assert [k["kid"] for k in keys] == [
        thumbprint(signer.public_key()),
        thumbprint(extra.public_key()),
    ]
    assert key_set.signing_kid == keys[0]["kid"]
    assert key_set.extra_kids == (keys[1]["kid"],)
    for k in keys:
        assert set(k) == {"kty", "crv", "x", "y", "kid", "alg", "use"}
        assert (k["kty"], k["crv"], k["alg"], k["use"]) == ("EC", "P-256", "ES256", "sig")
        assert "d" not in k


def test_key_set_repr_has_no_private_material() -> None:
    signer = _p256()
    key_set = build_key_set(_priv_b64(signer), [])
    text = repr(key_set)
    d = _b64url(signer.private_numbers().private_value.to_bytes(32, "big"))
    assert d not in text
    assert str(signer.private_numbers().private_value) not in text
    assert "PRIVATE" not in text
    assert "signing_key" not in text


def test_ephemeral_key_set_when_none() -> None:
    a = build_key_set(None, [])
    b = build_key_set(None, [])
    assert a.signing_kid != b.signing_kid
    assert a.sign({"sub": "x"}).count(".") == 2
