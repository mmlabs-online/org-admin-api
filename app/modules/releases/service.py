"""Staff reads and delete of the release log (ADM-033, ADR-0021). HTTP-free (R1)."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableError
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.audit import diff
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.releases.repository import AppReleaseRepository, ListFilter, ReleaseView
from app.modules.releases.schemas import ReleaseSort

logger = logging.getLogger(__name__)


class ReleaseService:  # `list` is defined last: it shadows the builtin in the class body
    def __init__(self, session: AsyncSession) -> None:
        self.repo = AppReleaseRepository(session)
        self.audit = AuditLogger(session)

    async def get(self, release_id: uuid.UUID) -> ReleaseView:
        view = await self.repo.get_view(release_id)
        if view is None:
            raise NotFoundError("Release not found")
        return view

    async def get_detail(self, release_id: uuid.UUID) -> tuple[ReleaseView, tuple[int, int]]:
        """The release and its linked bug-report counts (open, done)."""
        view = await self.get(release_id)
        return view, await self.repo.bug_report_counts(release_id)

    async def live(
        self, app_key: str | None, environment: str | None, *, limit: int, offset: int
    ) -> tuple[list[ReleaseView], int]:
        return await self.repo.live_page(app_key, environment, limit=limit, offset=offset)

    async def delete(
        self, release_id: uuid.UUID, *, actor: AdminPrincipal, ctx: AuditContext
    ) -> None:
        deleted = await self.repo.delete_returning(release_id)
        if deleted is None:
            raise NotFoundError("Release not found")
        app_key = await self.repo.app_key(deleted.app_id)
        # Ids and status only: never titles or URLs (ADR-0021 §8).
        await self.audit.record(
            AuditAction.RELEASE_DELETED,
            actor=actor,
            ctx=ctx,
            target=(AuditTarget.RELEASE, release_id),
            app_key=app_key,
            changes=diff.diff(
                {
                    "environment": deleted.environment,
                    "version": deleted.version,
                    "status": deleted.status,
                    "changes_count": deleted.changes_count,
                },
                None,
            ),
        )
        logger.info("release_deleted release_id=%s actor=%s", release_id, actor.user_id)

    async def list(
        self, f: ListFilter, sort: ReleaseSort, *, limit: int, offset: int
    ) -> tuple[list[ReleaseView], int]:
        if (
            f.created_from is not None
            and f.created_to is not None
            and f.created_from > f.created_to
        ):
            raise UnprocessableError(
                "created_from must not be after created_to",
                details=[{"field": "created_from", "reason": "after_to"}],
            )
        return await self.repo.page(f, sort, limit=limit, offset=offset)
