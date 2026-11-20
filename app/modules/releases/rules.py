"""Pure decision table of the app release write (ADM-034, spec OQ37, ADR-0021 §4). No I/O.

`Doc` holds the five fields the app write owns, in JSON form (`images` / `changes` as lists of
dicts with every key present). Empty means `None` for a scalar and `[]` for a list; `False` is a
value. Lists compare by order.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.modules.releases.models import ReleaseStatus

FIELDS = ("git_sha", "deployment_pr", "has_migrations", "images", "changes")

Doc = Mapping[str, Any]


class DecisionKind(StrEnum):
    CREATE = "create"
    REPLACE = "replace"  # pending: full replacement
    ENRICH = "enrich"  # deployed / failed: fill empty fields only
    NOOP = "noop"
    CONFLICT = "conflict"  # deployed / failed: a non-empty field differs


@dataclass(frozen=True)
class StoredDoc:
    status: str
    doc: Doc


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    fields: tuple[str, ...] = ()  # names only: the fields to write, or the ones that differ


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, list) and not value)


def decide_app_write(stored: StoredDoc | None, incoming: Doc) -> Decision:
    if stored is None:
        return Decision(DecisionKind.CREATE)
    differing = tuple(f for f in FIELDS if stored.doc[f] != incoming[f])
    if stored.status == ReleaseStatus.PENDING.value:
        return (
            Decision(DecisionKind.REPLACE, differing) if differing else Decision(DecisionKind.NOOP)
        )
    conflicts = tuple(f for f in differing if not _empty(stored.doc[f]))
    if conflicts:
        return Decision(DecisionKind.CONFLICT, conflicts)
    fill = tuple(f for f in differing if not _empty(incoming[f]))
    return Decision(DecisionKind.ENRICH, fill) if fill else Decision(DecisionKind.NOOP)
