"""Pure decision table of the app release write (ADM-034, spec OQ37, ADR-0021 §4). No I/O.

`Doc` holds the five fields the app write owns, in JSON form (`images` / `changes` as lists of
dicts with every key present). Empty means `None` for a scalar and `[]` for a list; `False` is a
value. Lists compare by order.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
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


MAX_FUTURE = timedelta(minutes=5)
MAX_AGE = timedelta(days=30)


def deployed_at_window_error(deployed_at: datetime, now: datetime) -> str | None:
    """OQ38: at most 5 minutes ahead and at most 30 days back."""
    if deployed_at > now + MAX_FUTURE:
        return "too_far_in_future"
    if deployed_at < now - MAX_AGE:
        return "too_old"
    return None


@dataclass(frozen=True)
class Deployment:
    """The three fields the deploy report owns (spec OQ38)."""

    status: str
    deployed_at: datetime | None
    deploy_run_url: str | None


class ReportKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    NOOP = "noop"
    CONFLICT = "conflict"  # deployed -> failed


@dataclass(frozen=True)
class ReportDecision:
    kind: ReportKind
    fields: tuple[str, ...] = ()  # names whose value actually changes


def decide_report(stored: Deployment | None, incoming: Deployment) -> ReportDecision:
    """ADR-0021 §5 plus the stale guard: an older `deployed_at` never replaces a newer one."""
    if stored is None:
        return ReportDecision(ReportKind.CREATE)
    same_url = stored.deploy_run_url == incoming.deploy_run_url
    if incoming.status == ReleaseStatus.FAILED.value:
        if stored.status == ReleaseStatus.DEPLOYED.value:
            return ReportDecision(ReportKind.CONFLICT)
        if stored.status == ReleaseStatus.PENDING.value:
            return ReportDecision(ReportKind.UPDATE, ("status", "deploy_run_url"))
        if same_url:
            return ReportDecision(ReportKind.NOOP)
        return ReportDecision(ReportKind.UPDATE, ("deploy_run_url",))
    if stored.status != ReleaseStatus.DEPLOYED.value:
        return ReportDecision(ReportKind.UPDATE, ("status", "deployed_at", "deploy_run_url"))
    if same_url:
        return ReportDecision(ReportKind.NOOP)
    assert stored.deployed_at is not None and incoming.deployed_at is not None  # noqa: S101
    if incoming.deployed_at < stored.deployed_at:
        return ReportDecision(ReportKind.NOOP)  # stale: a late retry of an older run
    if incoming.deployed_at == stored.deployed_at:
        return ReportDecision(ReportKind.UPDATE, ("deploy_run_url",))
    return ReportDecision(ReportKind.UPDATE, ("deployed_at", "deploy_run_url"))
