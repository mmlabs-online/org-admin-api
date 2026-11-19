"""Staff (Bearer, R3 class 1) view and revoke of an organization's invitations (USR-23).

Separate from the cookie-class `InvitationService`. Never reads or returns the token, its hash,
the email HMAC or an invitee email.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.invitations.repository import InvitationRepository, InvitationRow
from app.modules.invitations.schemas import InvitationStatus, OrgInvitationRead
from app.modules.organizations.repository import OrganizationRepository
from app.modules.rbac.actor import Actor


def _read(row: InvitationRow) -> OrgInvitationRead:
    return OrgInvitationRead(
        id=row[0],
        app=row[1],
        role_id=row[2],
        role_key=row[3],
        invited_by=row[4],
        created_at=row[5],
        expires_at=row[6],
        accepted_at=row[7],
        accepted_by=row[8],
        revoked_at=row[9],
        status=InvitationStatus(row[10]),
    )


class InvitationAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = InvitationRepository(session)
        self.orgs = OrganizationRepository(session)
        self.audit = AuditLogger(session)

    async def _require_org(self, org_id: uuid.UUID) -> None:
        if await self.orgs.get(org_id) is None:
            raise NotFoundError(f"Organization {org_id} not found")

    async def list_for_org(
        self, org_id: uuid.UUID, *, status: InvitationStatus | None, limit: int, offset: int
    ) -> tuple[list[OrgInvitationRead], int]:
        await self._require_org(org_id)
        rows, total = await self.repo.page_for_org(
            org_id, status=status, limit=limit, offset=offset
        )
        return [_read(r) for r in rows], total

    async def revoke(
        self, org_id: uuid.UUID, invitation_id: uuid.UUID, *, actor: Actor, ctx: AuditContext
    ) -> None:
        """Pending or expired-unrevoked: revoked and audited. Already revoked: no-op (204).
        Accepted: 409. Unknown or of another org: 404."""
        await self._require_org(org_id)
        app_id = await self.repo.revoke_for_org(org_id, invitation_id)
        if app_id is not None:
            await self.audit.record(
                AuditAction.ORGANIZATION_INVITATION_REVOKED,
                actor=actor,
                ctx=ctx,
                target=(AuditTarget.INVITATION, invitation_id),
                app_key=await self.repo.app_key(app_id),
                org_id=org_id,
                changes={"reason": "staff"},
            )
            return
        state = await self.repo.get_state(org_id, invitation_id)
        if state is None:
            raise NotFoundError("Invitation not found")
        if state[0] is not None:
            raise ConflictError(
                "Invitation was already accepted",
                details=[{"field": "invitation_id", "reason": "invitation_accepted"}],
            )
