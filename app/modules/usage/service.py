"""Staff management of usage metrics (ADM-039, ADR-0024). HTTP-free (R1).

The service takes any `Actor` and never checks permissions (the route dependency does): the seam
ADM-044 widens with an app-admin actor. Log lines never carry the `reason` (R5, R11).
"""

import logging
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PreconditionFailedError
from app.core.etag import IfMatch
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.rbac.actor import Actor, actor_user_id
from app.modules.rbac.models import ADMIN_APP_KEY, App
from app.modules.rbac.repository import RbacRepository
from app.modules.usage.errors import UsageMetricExistsError
from app.modules.usage.models import UsageMetric, UsageMetricStatus
from app.modules.usage.repository import UsageMetricRepository, UsageMetricView, view
from app.modules.usage.schemas import UsageMetricCreate, UsageMetricUpdate

logger = logging.getLogger(__name__)

_FIELDS = ("display_name", "default_limit", "period", "status")


class UsageMetricService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = UsageMetricRepository(session)
        self.rbac = RbacRepository(session)
        self.audit = AuditLogger(session)

    async def _app(self, app_key: str) -> App:
        if app_key == ADMIN_APP_KEY:  # the admin app has no usage metrics
            raise NotFoundError("App not found")
        app = await self.rbac.get_app_by_key(app_key)
        if app is None:
            raise NotFoundError("App not found")
        return app

    async def list(
        self, app_key: str, *, status: UsageMetricStatus | None, limit: int, offset: int
    ) -> tuple[list[UsageMetricView], int]:
        app = await self._app(app_key)
        items, total = await self.repo.page(
            app.id, status=None if status is None else status.value, limit=limit, offset=offset
        )
        return [view(m, app.key) for m in items], total

    async def get(self, app_key: str, metric_id: uuid.UUID) -> UsageMetricView:
        app = await self._app(app_key)
        metric = await self.repo.get(app.id, metric_id)
        if metric is None:
            raise NotFoundError("Usage metric not found")
        return view(metric, app.key)

    async def create(
        self, app_key: str, data: UsageMetricCreate, *, actor: Actor, ctx: AuditContext
    ) -> UsageMetricView:
        app = await self._app(app_key)
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
        app = await self._app(app_key)
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
