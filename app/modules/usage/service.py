"""Staff management of usage metrics (ADM-039, ADR-0024). HTTP-free (R1).

The service takes any `Actor` and never checks permissions (the route dependency does): the seam
ADM-044 widens with an app-admin actor. Log lines never carry the `reason` (R5, R11).
"""

import logging
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PreconditionFailedError, UnprocessableError
from app.core.etag import IfMatch
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.rbac.actor import Actor, actor_user_id
from app.modules.rbac.models import ADMIN_APP_KEY, App
from app.modules.rbac.repository import RbacRepository
from app.modules.usage.errors import UsageMetricExistsError
from app.modules.usage.models import UsageLimitOverride, UsageMetric, UsageMetricStatus
from app.modules.usage.repository import (
    UsageLimitOverrideRepository,
    UsageLimitOverrideView,
    UsageMetricRepository,
    UsageMetricView,
    view,
)
from app.modules.usage.schemas import (
    UsageLimitOverrideRemove,
    UsageLimitOverrideSet,
    UsageMetricCreate,
    UsageMetricUpdate,
)
from app.modules.users.repository import UserRepository

logger = logging.getLogger(__name__)

_FIELDS = ("display_name", "default_limit", "period", "status")


async def resolve_app(rbac: RbacRepository, app_key: str) -> App:
    if app_key == ADMIN_APP_KEY:  # the admin app has no usage metrics
        raise NotFoundError("App not found")
    app = await rbac.get_app_by_key(app_key)
    if app is None:
        raise NotFoundError("App not found")
    return app


class UsageMetricService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = UsageMetricRepository(session)
        self.rbac = RbacRepository(session)
        self.audit = AuditLogger(session)

    async def list(
        self, app_key: str, *, status: UsageMetricStatus | None, limit: int, offset: int
    ) -> tuple[list[UsageMetricView], int]:
        app = await resolve_app(self.rbac, app_key)
        items, total = await self.repo.page(
            app.id, status=None if status is None else status.value, limit=limit, offset=offset
        )
        return [view(m, app.key) for m in items], total

    async def get(self, app_key: str, metric_id: uuid.UUID) -> UsageMetricView:
        app = await resolve_app(self.rbac, app_key)
        metric = await self.repo.get(app.id, metric_id)
        if metric is None:
            raise NotFoundError("Usage metric not found")
        return view(metric, app.key)

    async def create(
        self, app_key: str, data: UsageMetricCreate, *, actor: Actor, ctx: AuditContext
    ) -> UsageMetricView:
        app = await resolve_app(self.rbac, app_key)
        exists = UsageMetricExistsError("The app already has a usage metric with this key")
        if await self.repo.find_by_key(app.id, data.key) is not None:
            raise exists
        metric = UsageMetric(
            app_id=app.id,
            key=data.key,
            display_name=data.display_name,
            unit=data.unit,
            default_limit=data.default_limit,
            period=data.period.value,
        )
        try:
            async with self.repo.session.begin_nested():
                await self.repo.add(metric)
        except IntegrityError as exc:  # concurrent create of the same (app, key)
            raise exists from exc
        await self.audit.record(
            AuditAction.USAGE_METRIC_CREATED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.USAGE_METRIC, metric.id),
            app_key=app.key,
            org_id=None,
            changes=diff.diff(None, diff.snapshot(metric, diff.USAGE_METRIC))
            | {"reason": data.reason},
        )
        logger.info(
            "usage_metric_created app=%s metric=%s actor=%s",
            app.key,
            metric.key,
            actor_user_id(actor),
        )
        return view(metric, app.key)

    async def update(
        self,
        app_key: str,
        metric_id: uuid.UUID,
        data: UsageMetricUpdate,
        *,
        actor: Actor,
        ctx: AuditContext,
        precondition: IfMatch | None = None,
    ) -> UsageMetricView:
        app = await resolve_app(self.rbac, app_key)
        metric = await self.repo.get_for_update(app.id, metric_id)
        if metric is None:
            raise NotFoundError("Usage metric not found")
        if (
            precondition is not None
            and not precondition.any
            and metric.updated_at not in precondition.versions
        ):
            raise PreconditionFailedError(metric.updated_at)
        before = diff.snapshot(metric, diff.USAGE_METRIC)
        sent = data.model_dump(exclude_unset=True, mode="json")
        values: dict[str, Any] = {
            f: sent[f] for f in _FIELDS if f in sent and sent[f] != getattr(metric, f)
        }
        if not values:
            return view(metric, app.key)
        await self.repo.apply(metric.id, values)
        after = diff.snapshot(metric, diff.USAGE_METRIC) | {
            f: diff.to_json(v) for f, v in values.items()
        }
        changes = diff.diff(before, after)
        status_change = changes.pop("status", None)
        rows: list[tuple[AuditAction, dict[str, Any]]] = []
        if changes:
            rows.append((AuditAction.USAGE_METRIC_UPDATED, changes))
        if status_change is not None:
            archived = status_change["to"] == UsageMetricStatus.ARCHIVED.value
            action = (
                AuditAction.USAGE_METRIC_ARCHIVED if archived else AuditAction.USAGE_METRIC_RESTORED
            )
            rows.append((action, {"status": status_change}))
        for action, row_changes in rows:
            await self.audit.record(
                action,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.USAGE_METRIC, metric.id),
                app_key=app.key,
                org_id=None,
                changes=row_changes | {"reason": data.reason},
            )
        logger.info(
            "usage_metric_updated app=%s metric=%s actor=%s",
            app.key,
            metric.key,
            actor_user_id(actor),
        )
        return view(await self._reload(app.id, metric.id), app.key)

    async def _reload(self, app_id: uuid.UUID, metric_id: uuid.UUID) -> UsageMetric:
        metric = await self.repo.get(app_id, metric_id)
        assert metric is not None  # noqa: S101 (locked in this transaction)
        return metric


class UsageLimitOverrideService:
    """Per-user limit overrides (ADM-040). Actor-generic like `UsageMetricService` (ADM-044 seam).

    The reason goes into the audit entry only; log lines carry ids (R5, R11).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.repo = UsageLimitOverrideRepository(session)
        self.metrics = UsageMetricRepository(session)
        self.users = UserRepository(session)
        self.rbac = RbacRepository(session)
        self.audit = AuditLogger(session)

    async def _metric(self, app_key: str, metric_id: uuid.UUID) -> tuple[App, UsageMetric]:
        app = await resolve_app(self.rbac, app_key)
        metric = await self.metrics.get(app.id, metric_id)
        if metric is None:
            raise NotFoundError("Usage metric not found")
        return app, metric

    async def _resolve(
        self, app_key: str, metric_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[App, UsageMetric]:
        app, metric = await self._metric(app_key, metric_id)
        if await self.users.get(user_id) is None:
            raise NotFoundError("User not found")
        return app, metric

    async def list(
        self, app_key: str, metric_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[UsageLimitOverrideView], int]:
        app, metric = await self._metric(app_key, metric_id)
        return await self.repo.page(app.key, metric.id, limit=limit, offset=offset)

    async def get(
        self, app_key: str, metric_id: uuid.UUID, user_id: uuid.UUID
    ) -> UsageLimitOverrideView:
        app, metric = await self._resolve(app_key, metric_id, user_id)
        found = await self.repo.get_view(app.key, user_id, metric.id)
        if found is None:
            raise NotFoundError("Usage limit override not found")
        return found

    async def set(
        self,
        app_key: str,
        metric_id: uuid.UUID,
        user_id: uuid.UUID,
        data: UsageLimitOverrideSet,
        *,
        actor: Actor,
        ctx: AuditContext,
    ) -> tuple[UsageLimitOverrideView, bool]:
        """Create or replace; returns the view and whether it was created."""
        app, metric = await self._resolve(app_key, metric_id, user_id)
        if data.expires_at is not None and data.expires_at <= await self.repo.db_now():
            raise UnprocessableError(
                "expires_at must be in the future",
                details=[{"field": "expires_at", "reason": "not_in_future"}],
            )
        existing = await self.repo.get_for_update(user_id, metric.id)
        created = False
        if existing is None:
            existing = await self.repo.insert(
                user_id=user_id,
                metric_id=metric.id,
                limit_value=data.limit,
                expires_at=data.expires_at,
                created_by=actor_user_id(actor),
            )
            if existing is not None:
                created = True
                await self._audit(
                    AuditAction.USAGE_LIMIT_OVERRIDE_SET,
                    app,
                    existing,
                    diff.diff(None, diff.snapshot(existing, diff.USAGE_LIMIT_OVERRIDE)),
                    data.reason,
                    actor,
                    ctx,
                )
            else:  # a concurrent insert won: replace its row
                existing = await self.repo.get_for_update(user_id, metric.id)
        if not created:
            assert existing is not None  # noqa: S101 (the unique row exists)
            if (existing.limit_value, existing.expires_at) != (data.limit, data.expires_at):
                before = diff.snapshot(existing, diff.USAGE_LIMIT_OVERRIDE)
                await self.repo.apply(
                    existing.id, {"limit_value": data.limit, "expires_at": data.expires_at}
                )
                after = before | {
                    "limit_value": data.limit,
                    "expires_at": diff.to_json(data.expires_at),
                }
                await self._audit(
                    AuditAction.USAGE_LIMIT_OVERRIDE_UPDATED,
                    app,
                    existing,
                    diff.diff(before, after),
                    data.reason,
                    actor,
                    ctx,
                )
        logger.info(
            "usage_override_%s app=%s metric=%s user=%s actor=%s",
            "set" if created else "updated",
            app.key,
            metric.id,
            user_id,
            actor_user_id(actor),
        )
        v = await self.repo.get_view(app.key, user_id, metric.id)
        assert v is not None  # noqa: S101 (locked in this transaction)
        return v, created

    async def remove(
        self,
        app_key: str,
        metric_id: uuid.UUID,
        user_id: uuid.UUID,
        data: UsageLimitOverrideRemove,
        *,
        actor: Actor,
        ctx: AuditContext,
    ) -> None:
        app, metric = await self._resolve(app_key, metric_id, user_id)
        old = await self.repo.delete(user_id, metric.id)
        if old is None:
            raise NotFoundError("Usage limit override not found")
        await self._audit(
            AuditAction.USAGE_LIMIT_OVERRIDE_REMOVED,
            app,
            old,
            diff.diff(diff.snapshot(old, diff.USAGE_LIMIT_OVERRIDE), None),
            data.reason,
            actor,
            ctx,
        )
        logger.info(
            "usage_override_removed app=%s metric=%s user=%s actor=%s",
            app.key,
            metric.id,
            user_id,
            actor_user_id(actor),
        )

    async def _audit(
        self,
        action: AuditAction,
        app: App,
        override: UsageLimitOverride,
        changes: dict[str, Any],
        reason: str,
        actor: Actor,
        ctx: AuditContext,
    ) -> None:
        await self.audit.record(
            action,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.USAGE_LIMIT_OVERRIDE, override.id),
            app_key=app.key,
            org_id=None,
            changes=changes | {"reason": reason},
        )
