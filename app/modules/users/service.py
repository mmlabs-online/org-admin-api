import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import LastSuperAdminError, NotFoundError, SelfStatusChangeForbiddenError
from app.modules.rbac.actor import Actor, actor_user_id, require_super
from app.modules.rbac.models import ADMIN_APP_KEY
from app.modules.rbac.repository import RbacRepository
from app.modules.rbac.service import SUPER_ADMIN_ROLE
from app.modules.sessions.repository import SessionRepository
from app.modules.users.models import User, UserStatus
from app.modules.users.repository import UserRepository


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = UserRepository(session)

    async def get(self, user_id: uuid.UUID) -> User:
        user = await self.repo.get(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def list(
        self, *, email: str | None, status: UserStatus | None, limit: int, offset: int
    ) -> tuple[list[User], int]:
        return await self.repo.list(email=email, status=status, limit=limit, offset=offset)

    async def set_status(self, user_id: uuid.UUID, status: UserStatus, *, actor: Actor) -> User:
        """Lock order `admin` app -> user row (as `bootstrap_super_admin`), so the
        last-super-admin guard is serialized with revokes and other status changes."""
        rbac = RbacRepository(self.repo.session)
        admin = await rbac.lock_app(ADMIN_APP_KEY)
        user = await self.repo.get_for_update(user_id)
        if user is None:
            raise NotFoundError("User not found")
        if user.status == status.value:
            return user  # no write: updated_at stays unchanged
        if user.id == actor_user_id(actor):
            raise SelfStatusChangeForbiddenError("Cannot change your own status")
        if await rbac.user_has_privileged_assignment(user.id):  # G5: disable and re-enable
            require_super(actor)
        if status == UserStatus.DISABLED and admin is not None:
            role = await rbac.get_role_by_key(admin.id, SUPER_ADMIN_ROLE)
            ra = None if role is None else await rbac.find_assignment(user.id, role.id, None)
            if (
                role is not None
                and ra is not None
                and await rbac.is_counting_super_admin(role.id, ra.id)
                and await rbac.count_other_active_super_admins(role.id, ra.id) == 0
            ):
                raise LastSuperAdminError("Cannot disable the last active super-admin")
        # R4: audit entry in the same transaction once ADM-004 lands.
        user.status = status.value
        await self.repo.session.flush()
        if status == UserStatus.DISABLED:
            # Admin tokens stop on the next request (per-request reload); app tokens expire.
            await SessionRepository(self.repo.session).revoke_all_accounts_of_user(user.id)
        await self.repo.session.refresh(user)
        return user
