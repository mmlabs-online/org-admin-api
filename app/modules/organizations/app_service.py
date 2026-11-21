"""App-facing read of an org's members (USR-29, ADR-0025). HTTP-free (R1).

Audit rows carry ids and counts only: never emails, names or the response body (R11).
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.app_credentials.service import AuthenticatedApp
from app.modules.audit.actions import AuditAction, AuditOutcome, AuditTarget
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.organizations.auth_service import _org_not_supported
from app.modules.organizations.errors import OrgNotVisibleToAppError
from app.modules.organizations.schemas import AppOrgMemberRead
from app.modules.rbac.models import OrgMode
from app.modules.rbac.repository import RbacRepository
from app.modules.users.models import UserStatus


class AppOrgMemberService:
    def __init__(self, session: AsyncSession) -> None:
        self.rbac = RbacRepository(session)
        self.audit = AuditLogger(session)

    async def list(
        self,
        caller: AuthenticatedApp,
        org_id: uuid.UUID,
        *,
        status: UserStatus | None,
        updated_since: datetime | None,
        limit: int,
        offset: int,
        ctx: AuditContext,
    ) -> tuple[list[AppOrgMemberRead], int]:
        app = await self.rbac.get_app_by_key(caller.app_key)
        if app is None or app.org_mode != OrgMode.REQUIRED.value:
            raise _org_not_supported()
        if not await self.rbac.org_related_to_app(org_id, caller.app_id):
            await self.audit.record_failure(
                AuditAction.ORGANIZATION_MEMBERS_READ,
                outcome=AuditOutcome.DENIED,
                actor=caller.actor,
                ctx=ctx,
                target=(AuditTarget.ORGANIZATION, org_id),
                app_key=caller.app_key,
                org_id=org_id,
                changes={"reason": OrgNotVisibleToAppError.code},
            )
            raise OrgNotVisibleToAppError("Organization not found")
        rows, total = await self.rbac.page_app_org_members(
            org_id,
            caller.app_id,
            status=None if status is None else status.value,
            updated_since=updated_since,
            limit=limit,
            offset=offset,
        )
        await self.audit.record(
            AuditAction.ORGANIZATION_MEMBERS_READ,
            actor=caller.actor,
            ctx=ctx,
            target=(AuditTarget.ORGANIZATION, org_id),
            app_key=caller.app_key,
            org_id=org_id,
            changes={
                "returned": len(rows),
                "total": total,
                "limit": limit,
                "offset": offset,
                "status_filter": None if status is None else status.value,
                "updated_since_set": updated_since is not None,
            },
        )
        items = []
        for r in rows:
            disabled = r.status == UserStatus.DISABLED.value
            items.append(
                AppOrgMemberRead(
                    user_id=r.user_id,
                    email=None if disabled else r.email,
                    email_verified=r.email_verified,
                    display_name=None if disabled else r.display_name,
                    status=UserStatus(r.status),
                    org_roles=r.org_roles,
                    joined_at=r.joined_at,
                )
            )
        return items, total
