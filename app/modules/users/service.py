import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
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

    async def set_status(self, user_id: uuid.UUID, status: UserStatus) -> User:
        user = await self.get(user_id)
        if user.status == status.value:
            return user  # no write: updated_at stays unchanged
        # R4: audit entry in the same transaction once ADM-004 lands.
        # USR-03/03b must reject login, revoke device sessions and refuse tokens for disabled users.
        user.status = status.value
        await self.repo.session.flush()
        await self.repo.session.refresh(user)
        return user
