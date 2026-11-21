"""The deployer's deploy report (ADM-036, ADR-0021 §4-§8). HTTP-free (R1).

A first report creates the record (`INSERT ... ON CONFLICT DO NOTHING`); an existing record is read
`FOR UPDATE` and decided by the pure table in `rules.py`. A no-op and a conflict write nothing.
Audit rows carry ids and status only, never the run URL or the timestamp. No `lock_app`: the
deployer touches no RBAC data and meets the app write only on the release row.
"""

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.deployer_credentials.service import AuthenticatedDeployer
from app.modules.releases.errors import ReleaseAlreadyDeployedError
from app.modules.releases.models import AppRelease, ReleaseCreatedVia, ReleaseEnvironment
from app.modules.releases.repository import AppReleaseRepository, ReleaseView
from app.modules.releases.rules import (
    Deployment,
    ReportKind,
    decide_report,
    deployed_at_window_error,
)
from app.modules.releases.write_schemas import ReleaseDeploymentReport

logger = logging.getLogger(__name__)


class ReleaseDeploymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AppReleaseRepository(session)
        self.audit = AuditLogger(session)

    async def report(
        self,
        caller: AuthenticatedDeployer,
        key: str,
        environment: ReleaseEnvironment,
        version: str,
        body: ReleaseDeploymentReport,
        *,
        ctx: AuditContext,
    ) -> tuple[ReleaseView, bool]:
        """Returns the record and whether it was created (201) or not (200)."""
        app_id = await self.repo.app_id_by_key(key)
        if app_id is None:
            raise NotFoundError("App not found")
        if body.deployed_at is not None:
            reason = deployed_at_window_error(body.deployed_at, await self.repo.db_now())
            if reason is not None:
                raise UnprocessableError(
                    "deployed_at is outside the accepted window",
                    details=[{"field": "deployed_at", "reason": reason}],
                )
        incoming = Deployment(body.status, body.deployed_at, body.deploy_run_url)
        row: AppRelease | None = None
        for _ in range(2):
            created = await self.repo.insert_if_absent(
                app_id=app_id,
                environment=environment.value,
                version=version,
                created_via=ReleaseCreatedVia.DEPLOYER.value,
                status=body.status,
                deployed_at=body.deployed_at,
                deploy_run_url=body.deploy_run_url,
                images=[],
                changes=[],
            )
            if created is not None:
                await self._record(
                    caller,
                    ctx,
                    key,
                    AuditAction.RELEASE_CREATED,
                    created,
                    diff.diff(
                        None,
                        {
                            "environment": created.environment,
                            "version": created.version,
                            "status": created.status,
                            "created_via": created.created_via,
                        },
                    ),
                )
                return await self._view(created.id), True
            row = await self.repo.get_for_update(app_id, environment.value, version)
            if row is not None:
                break
        if row is None:  # deleted concurrently, twice in a row: practically unreachable
            raise ConflictError(
                "Release changed concurrently; retry",
                details=[{"field": "version", "reason": "retry"}],
            )

        decision = decide_report(
            Deployment(row.status, row.deployed_at, row.deploy_run_url), incoming
        )
        if decision.kind is ReportKind.CONFLICT:
            logger.warning(
                "release_already_deployed app=%s env=%s version=%s release_id=%s key_id=%s",
                key,
                environment.value,
                version,
                row.id,
                caller.key_id,
            )
            raise ReleaseAlreadyDeployedError(
                "Release is already deployed; a failed report cannot replace it"
            )
        if decision.kind is ReportKind.NOOP:
            return await self._view(row.id), False
        before_status = row.status
        for field_name in decision.fields:
            setattr(row, field_name, getattr(incoming, field_name))
        await self.session.flush()
        changes: dict[str, Any] = {
            "environment": diff.change(row.environment, row.environment),
            "version": diff.change(row.version, row.version),
            "fields": diff.change(None, sorted(decision.fields)),
        }
        if "status" in decision.fields:
            changes["status"] = diff.change(before_status, row.status)
        await self._record(caller, ctx, key, AuditAction.RELEASE_DEPLOYMENT_REPORTED, row, changes)
        return await self._view(row.id), False

    async def _view(self, release_id: uuid.UUID) -> ReleaseView:
        view = await self.repo.get_view(release_id)
        assert view is not None  # noqa: S101 (row locked or just inserted in this transaction)
        return view

    async def _record(
        self,
        caller: AuthenticatedDeployer,
        ctx: AuditContext,
        key: str,
        action: AuditAction,
        row: AppRelease,
        changes: dict[str, Any],
    ) -> None:
        await self.audit.record(
            action,
            actor=caller.actor,
            ctx=ctx,
            target=(AuditTarget.RELEASE, row.id),
            app_key=key,
            changes=changes,
        )
