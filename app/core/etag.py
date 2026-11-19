"""Strong ETags over exact response bytes and `If-None-Match` matching (RFC 9110 §13.1.2)."""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

MAX_IF_NONE_MATCH = 2048
MAX_IF_MATCH = 2048
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_VERSION_TAG = re.compile(r'"([0-9]{1,18})"')

ETAG_RESPONSE_HEADER: dict[str, Any] = {
    "description": "Strong version tag of the resource (derived from `updated_at`).",
    "schema": {"type": "string"},
}
IF_MATCH_DESCRIPTION = (
    "Optional precondition (ADM-024): the `ETag` the client last saw, a comma-separated list of "
    "strong tags, or `*`. A stale, weak or malformed value gives 412 `precondition_failed` and "
    "changes nothing. Absent: the update is unconditional."
)


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


def version_etag(updated_at: datetime) -> str:
    """Strong ETag of a stored row: `"` + microseconds since the Unix epoch (UTC) + `"`.

    It validates the stored row only; derived fields (e.g. an entitlement's `active`) can change
    without a new tag.
    """
    return '"' + str((updated_at.astimezone(UTC) - _EPOCH) // _MICROSECOND) + '"'


@dataclass(frozen=True)
class IfMatch:
    any: bool = False
    versions: frozenset[datetime] = frozenset()


def parse_if_match(values: list[str] | None) -> IfMatch | None:
    """`None` when absent (unconditional). Otherwise the `updated_at` values the client accepts.

    Strong comparison (RFC 9110 §13.1.1): weak tags, a `*` inside a list, malformed entries and
    headers over `MAX_IF_MATCH` chars match nothing, so the caller answers 412.
    """
    header = ",".join(values or [])
    if not header.strip():
        return None
    if len(header) > MAX_IF_MATCH:
        return IfMatch()
    if header.strip() == "*":
        return IfMatch(any=True)
    versions: set[datetime] = set()
    for raw in header.split(","):
        if raw.strip() == "*":
            return IfMatch()  # `*` is only valid alone
        match = _VERSION_TAG.fullmatch(raw.strip())
        if match is None:
            continue
        try:
            versions.add(_EPOCH + timedelta(microseconds=int(match.group(1))))
        except OverflowError:
            continue
    return IfMatch(versions=frozenset(versions))
