"""Credential format `mmak_<key_id>_<secret>` (ADR-0016 §3). Pure functions, no ORM.

`key_id` is 16 hex chars (public, safe in logs and audit rows); the secret is 256 bits.
"""

import hashlib
import re
import secrets

PREFIX = "mmak_"
_MAX_LENGTH = 128  # cap before matching: the real value is 64 chars
_RE = re.compile(r"^mmak_([0-9a-f]{16})_([A-Za-z0-9_-]{43})\Z")


def new_credential() -> tuple[str, str]:
    """Returns `(key_id, raw)`; `raw` is shown once and never stored."""
    key_id = secrets.token_hex(8)
    return key_id, f"{PREFIX}{key_id}_{secrets.token_urlsafe(32)}"


def parse_credential(raw: str) -> tuple[str, str] | None:
    """`(key_id, secret)`, or None for anything that is not exactly this format."""
    if len(raw) > _MAX_LENGTH:
        return None
    match = _RE.match(raw)
    return (match.group(1), match.group(2)) if match else None


def secret_hash(secret: str) -> bytes:
    """A fast hash is enough for a 256-bit random secret (same rule as `device_token_hash`)."""
    return hashlib.sha256(secret.encode("utf-8")).digest()
