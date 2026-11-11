"""Strong ETags over exact response bytes and `If-None-Match` matching (RFC 9110 §13.1.2)."""

import hashlib

MAX_IF_NONE_MATCH = 2048


def strong_etag(body: bytes) -> str:
    """`"` + first 32 hex chars (128 bits) of SHA-256 of `body` + `"`."""
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def if_none_match(values: list[str], etag: str) -> bool:
    """True when the `If-None-Match` header values match `etag` (weak comparison).

    Several headers are joined; over `MAX_IF_NONE_MATCH` chars, `*` inside a list and malformed
    entries never match.
    """
    header = ",".join(values)
    if not header or len(header) > MAX_IF_NONE_MATCH:
        return False
    if header.strip() == "*":
        return True
    for raw in header.split(","):
        tag = raw.strip()
        if tag.startswith("W/"):
            tag = tag[2:]
        well_formed = len(tag) >= 2 and tag[0] == tag[-1] == '"' and '"' not in tag[1:-1]
        if well_formed and tag == etag:
            return True
    return False
