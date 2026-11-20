"""The app's release write (ADM-034, ADR-0021 §4, §8). HTTP-free (R1).

Lock order is app, then credential row (`confirm`), then release row. A first write is an
`INSERT ... ON CONFLICT DO NOTHING`; an existing record is read `FOR UPDATE` and decided by the
pure table in `rules.py`. A no-op and a conflict write nothing; audit rows carry ids, status and
counts only, never titles, URLs, digests or the sha.
"""

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.modules.app_credentials.service import AppCredentialService, AuthenticatedApp
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.releases.errors import ReleaseNotPendingError
from app.modules.releases.models import AppRelease, ReleaseCreatedVia, ReleaseEnvironment
from app.modules.releases.repository import AppReleaseRepository, ReleaseView
from app.modules.releases.rules import FIELDS, DecisionKind, StoredDoc, decide_app_write
from app.modules.releases.write_schemas import ReleasePut

logger = logging.getLogger(__name__)


def _doc(body: ReleasePut) -> dict[str, Any]:
    dumped = body.model_dump(mode="json")
    return {f: dumped[f] for f in FIELDS}


def _stored(row: AppRelease) -> StoredDoc:
    return StoredDoc(row.status, {f: getattr(row, f) for f in FIELDS})


def _audit_view(row: AppRelease) -> dict[str, Any]:
    return {
        "environment": row.environment,
        "version": row.version,
        "status": row.status,
        "changes_count": len(row.changes),
        "images_count": len(row.images),
    }


class ReleaseAppWriteService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AppReleaseRepository(session)
        self.audit = AuditLogger(session)

    async def put(
        self,
        caller: AuthenticatedApp,
        environment: ReleaseEnvironment,
        version: str,
        body: ReleasePut,
        *,
        ctx: AuditContext,
    ) -> tuple[ReleaseView, bool]:
        """Returns the record and whether it was created (201) or not (200)."""
        await AppCredentialService(self.session).confirm(caller)  # app lock first
        incoming = _doc(body)
        row: AppRelease | None = None
        for _ in range(2):
            created = await self.repo.insert_if_absent(
                app_id=caller.app_id,
                environment=environment.value,
                version=version,
                created_via=ReleaseCreatedVia.APP.value,
                **incoming,
            )
            if created is not None:
                await self._record(caller, ctx, AuditAction.RELEASE_CREATED, created)
                return await self._view(created.id), True
            row = await self.repo.get_for_update(caller.app_id, environment.value, version)
            if row is not None:
                break
        if row is None:  # deleted concurrently, twice in a row: practically unreachable
            raise ConflictError(
                "Release changed concurrently; retry",
                details=[{"field": "version", "reason": "retry"}],
            )

        decision = decide_app_write(_stored(row), incoming)
        if decision.kind is DecisionKind.CONFLICT:
            logger.warning(
                "release_not_pending_conflict app=%s env=%s version=%s release_id=%s key_id=%s "
                "fields=%s",
                caller.app_key,
                environment.value,
                version,
                row.id,
                caller.key_id,
                ",".join(decision.fields),
            )
            raise ReleaseNotPendingError(
                "The release is no longer pending and differs from the request",
                details=[{"field": f, "reason": "differs"} for f in decision.fields],
            )
        if decision.kind is DecisionKind.NOOP:
            return await self._view(row.id), False
        before = _audit_view(row)
        for field_name in decision.fields:
            setattr(row, field_name, incoming[field_name])
        await self.session.flush()
        await self._record(
            caller, ctx, AuditAction.RELEASE_UPDATED, row, before=before, fields=decision.fields
        )
        return await self._view(row.id), False

    async def _view(self, release_id: uuid.UUID) -> ReleaseView:
        view = await self.repo.get_view(release_id)
        assert view is not None  # noqa: S101 (row locked or just inserted in this transaction)
        return view

    async def _record(
        self,
        caller: AuthenticatedApp,
        ctx: AuditContext,
        action: AuditAction,
        row: AppRelease,
        *,
        before: dict[str, Any] | None = None,
        fields: tuple[str, ...] = (),
    ) -> None:
        changes = diff.diff(before, _audit_view(row))
        if fields:
            changes["fields"] = diff.change(None, sorted(fields))
        await self.audit.record(
            action,
            actor=caller.actor,
            ctx=ctx,
            target=(AuditTarget.RELEASE, row.id),
            app_key=caller.app_key,
            changes=changes,
        )
