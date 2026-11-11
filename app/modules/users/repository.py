import uuid

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User, UserStatus


def escape_like(term: str) -> str:
    """Escape LIKE wildcards so `term` matches literally (backslash first)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def list(
        self, *, email: str | None, status: UserStatus | None, limit: int, offset: int
    ) -> tuple[list[User], int]:
        conditions: list[ColumnElement[bool]] = []
        if email is not None:
            conditions.append(User.email.ilike(f"%{escape_like(email)}%", escape="\\"))
        if status is not None:
            conditions.append(User.status == status.value)
        total = await self.session.scalar(select(func.count()).select_from(User).where(*conditions))
        result = await self.session.execute(
            select(User)
            .where(*conditions)
            .order_by(User.created_at.desc(), User.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), total or 0

    async def add(self, user: User) -> User:
        """Insert in a savepoint so a unique violation leaves the outer transaction usable."""
        async with self.session.begin_nested():
            self.session.add(user)
            await self.session.flush()
        await self.session.refresh(user)
        return user
