"""Allowlisted before/after diffs (AC8, R11): the only source of `audit_log.changes`.

Never `model_dump()`, never an email, hash, token or cookie: only the fields listed here.
"""

import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import Enum
from typing import Any

ORG = ("slug", "name", "description", "status", "created_by")
APP = (
    "key",
    "name",
    "status",
    "join_policy",
    "org_mode",
    "origins",
    "default_role_id",
    "org_owner_role_id",
)
PERMISSION = ("key", "description", "is_admin", "scope")
ROLE = ("key", "name", "is_system")
USER = ("status",)
ENTITLEMENT = ("app_id", "plan", "limits", "enabled")
USAGE_METRIC = ("key", "display_name", "unit", "default_limit", "period", "status")

Change = Mapping[str, Any]  # {"from": ..., "to": ...}


def to_json(value: Any) -> Any:
    if isinstance(value, Enum):
        return to_json(value.value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): to_json(v) for k, v in value.items()}
    return value


def snapshot(obj: object, fields: Iterable[str]) -> dict[str, Any]:
    """JSON values of `fields`; call it before `setattr` in update services."""
    return {f: to_json(getattr(obj, f)) for f in fields}


def change(before: Any, after: Any) -> dict[str, Any]:
    return {"from": to_json(before), "to": to_json(after)}


def diff(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, Change]:
    """Changed fields only; `before=None` is a create, `after=None` a delete."""
    keys = list((before or after or {}).keys())
    out: dict[str, Change] = {}
    for key in keys:
        old = None if before is None else before.get(key)
        new = None if after is None else after.get(key)
        if old != new:
            out[key] = change(old, new)
    return out
