"""ADM-035: the `mmdk_` codec (ADR-0021 §2). Pure logic."""

import hashlib

import pytest

from app.modules.app_credentials import codec as app_codec
from app.modules.deployer_credentials.codec import new_credential, parse_credential, secret_hash


def test_round_trip() -> None:
    key_id, raw = new_credential()
    assert raw.startswith("mmdk_") and len(raw) == 65
    parsed = parse_credential(raw)
    assert parsed is not None and parsed[0] == key_id


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "mmdk_",
        app_codec.new_credential()[1],
        "a.b.c",
        "mmdk_" + "A" * 16 + "_" + "a" * 43,
        "mmdk_" + "a" * 15 + "_" + "a" * 43,
        "mmdk_" + "a" * 16 + "_" + "a" * 42,
        new_credential()[1] + "\n",
        "mmdk_" + "a" * 130,
    ],
)
def test_rejects_malformed(raw: str) -> None:
    assert parse_credential(raw) is None


def test_app_codec_rejects_deployer_key() -> None:
    assert app_codec.parse_credential(new_credential()[1]) is None


def test_secret_hash_is_sha256_32_bytes() -> None:
    assert secret_hash("x") == hashlib.sha256(b"x").digest() == app_codec.secret_hash("x")
    assert len(secret_hash("x")) == 32
