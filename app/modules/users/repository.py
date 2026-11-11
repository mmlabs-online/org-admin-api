from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, user: User) -> User:
        """Insert in a savepoint so a unique violation leaves the outer transaction usable."""
        async with self.session.begin_nested():
            self.session.add(user)
            await self.session.flush()
        await self.session.refresh(user)
        return user
