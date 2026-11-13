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

    async def get_for_update(self, user_id: uuid.UUID) -> User | None:
        return await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def mark_email_verified(self, user: User) -> None:
        """Set `email_verified_at` from the database clock."""
        user.email_verified_at = func.now()
        await self.session.flush()
        await self.session.refresh(user, ["email_verified_at"])

    async def get_by_email_for_share(self, email: str) -> User | None:
        """Exact (CITEXT) lookup of a normalized email, locked `FOR SHARE` until commit.

        Blocks a concurrent status change or delete; FK checks (`KEY SHARE`) are not blocked.
        """
        return await self.session.scalar(
            select(User)
            .where(User.email == email)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )

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
