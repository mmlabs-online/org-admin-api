"""`on_signup` hook (USR-04 plugs the verification email in here)."""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class SignupHook(Protocol):
    """Runs in the sign-up transaction. Only record/enqueue work; no slow I/O."""

    async def __call__(self, user_id: uuid.UUID, session: AsyncSession) -> None: ...


async def noop_signup_hook(user_id: uuid.UUID, session: AsyncSession) -> None:
    return None


def get_signup_hook() -> SignupHook:
    return noop_signup_hook
