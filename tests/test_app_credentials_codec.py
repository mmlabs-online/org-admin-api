import hashlib
import re

import pytest

from app.modules.app_credentials.codec import new_credential, parse_credential, secret_hash


def test_new_credential_format_and_entropy() -> None:
    key_id, raw = new_credential()
    assert re.fullmatch(r"mmak_[0-9a-f]{16}_[A-Za-z0-9_-]{43}", raw)
    assert raw.split("_")[1] == key_id
    assert new_credential()[1] != raw


def test_parse_round_trip() -> None:
    key_id, raw = new_credential()
    parsed = parse_credential(raw)
    assert parsed is not None
    assert parsed[0] == key_id
    assert raw.endswith(parsed[1])


_KEY = "0123456789abcdef"
_SECRET = "A" * 43


@pytest.mark.parametrize(
    "raw",
    [
        f"{_KEY}_{_SECRET}",  # no prefix
        f"mmak_{_KEY.upper()}_{_SECRET}",  # uppercase hex
        f"mmak_{_KEY[:15]}_{_SECRET}",  # short key_id
        f"mmak_{_KEY}0_{_SECRET}",  # long key_id
        f"mmak_{_KEY}_{_SECRET[:42]}",  # short secret
        f"mmak_{_KEY}_{_SECRET}A",  # long secret
        f"mmak_{_KEY}_{'+' * 43}",  # illegal chars
        f"mmak_{_KEY}_{_SECRET} ",  # trailing space
        f"mmak_{_KEY}_{_SECRET}\n",  # trailing newline
        "eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln",  # JWT-shaped
        "",
        "mmak_" + "a" * 10_000,
    ],
)
def test_parse_rejects_malformed(raw: str) -> None:
    assert parse_credential(raw) is None


def test_secret_hash_is_sha256_32_bytes() -> None:
    digest = secret_hash("abc")
    assert digest == hashlib.sha256(b"abc").digest()
    assert len(digest) == 32
