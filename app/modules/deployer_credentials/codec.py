"""Credential format `mmdk_<key_id>_<secret>` (ADR-0021 §2). Pure functions, no ORM.

Same shape and hash as `mmak_`, its own prefix: a wrong prefix is refused before any database
read, and the `mmak_` parser refuses this value in turn.
"""

import re
import secrets

from app.modules.app_credentials.codec import secret_hash

__all__ = ["PREFIX", "new_credential", "parse_credential", "secret_hash"]

PREFIX = "mmdk_"
_MAX_LENGTH = 128  # cap before matching: the real value is 64 chars
_RE = re.compile(r"^mmdk_([0-9a-f]{16})_([A-Za-z0-9_-]{43})\Z")


def new_credential() -> tuple[str, str]:
    """Returns `(key_id, raw)`; `raw` is shown once and never stored."""
    key_id = secrets.token_hex(8)
    return key_id, f"{PREFIX}{key_id}_{secrets.token_urlsafe(32)}"


def parse_credential(raw: str) -> tuple[str, str] | None:
    """`(key_id, secret)`, or None for anything that is not exactly this format."""
    if len(raw) > _MAX_LENGTH or not raw.startswith(PREFIX):
        return None
    match = _RE.match(raw)
    return (match.group(1), match.group(2)) if match else None
