import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.listing import escape_like
from app.db.base import bumped_updated_at
from app.modules.rbac.models import App, Role, RoleAssignment
from app.modules.users.models import User, UserStatus


def active_assignment_clause() -> ColumnElement[bool]:
    """`role_assignments` row is active: not expired by the database clock (spec §5.15).

    Shared by the users list access summary (USR-34) and its filters (USR-35). Same rule as
    `RbacRepository._is_active` (rbac/repository.py:597-599). Uncorrelated: callers add their own
    `RoleAssignment.user_id == ...` condition.
    """
    return or_(RoleAssignment.expires_at.is_(None), RoleAssignment.expires_at > func.now())


class AccessRow(NamedTuple):
    user_id: uuid.UUID
    assignment_id: uuid.UUID
    role_id: uuid.UUID
    role_key: str
    org_id: uuid.UUID | None
    expires_at: datetime | None
    app_key: str
    app_status: str


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_for_update(self, user_id: uuid.UUID) -> User | None:
        """Lock the user row `FOR NO KEY UPDATE` until commit (USR-04d S1).

        Conflicts with `FOR SHARE` (login, forgot) and other updates; does not block the FK
        `KEY SHARE` that an `email_tokens` INSERT takes, so it cannot deadlock with a resend.
        Callers update only non-key columns, so the lock is never upgraded.
        """
        return await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )

    async def mark_email_verified(self, user: User) -> None:
        """Set `email_verified_at` from the database clock."""
        user.email_verified_at = func.now()
        await self.session.flush()
        await self.session.refresh(user, ["email_verified_at"])

    async def set_display_name(self, user: User, value: str | None) -> None:
        """Write the name and bump `updated_at` strictly (USR-29 `updated_since` sees it)."""
        await self.session.execute(
            update(User)
            .where(User.id == user.id)
            .values(display_name=value, updated_at=bumped_updated_at(User.updated_at))
        )
        await self.session.refresh(user, ["display_name", "updated_at"])

    async def set_password_hash(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        await self.session.flush()

    async def get_for_share(self, user_id: uuid.UUID) -> User | None:
        """Lock the user row `FOR SHARE` until commit (ADM-002e, verification resend).

        Blocks a concurrent status change or delete; FK checks (`KEY SHARE`) are not blocked.
        Keep it `FOR SHARE`: resend takes it after the device row lock, login takes its user
        share lock before the device row, so a stronger mode here can deadlock the two.
        """
        return await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )

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

    async def list_access(self, user_ids: Sequence[uuid.UUID], *, per_user: int) -> list[AccessRow]:
        """Active assignments of the given users in one query, first `per_user` rows per user.

        Order per user: app key, role key (byte order), app scope before org scope, org id,
        assignment id. Apps in any status are included (OQ75).
        """
        if not user_ids:
            return []
        ranked = (
            select(
                RoleAssignment.user_id,
                RoleAssignment.id.label("assignment_id"),
                RoleAssignment.role_id,
                Role.key.label("role_key"),
                RoleAssignment.org_id,
                RoleAssignment.expires_at,
                App.key.label("app_key"),
                App.status.label("app_status"),
                func.row_number()
                .over(
                    partition_by=RoleAssignment.user_id,
                    order_by=(
                        App.key.collate("C"),
                        Role.key.collate("C"),
                        RoleAssignment.org_id.is_not(None),
                        RoleAssignment.org_id,
                        RoleAssignment.id,
                    ),
                )
                .label("rn"),
            )
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(App, App.id == Role.app_id)
            .where(RoleAssignment.user_id.in_(user_ids), active_assignment_clause())
            .subquery()
        )
        result = await self.session.execute(
            select(
                ranked.c.user_id,
                ranked.c.assignment_id,
                ranked.c.role_id,
                ranked.c.role_key,
                ranked.c.org_id,
                ranked.c.expires_at,
                ranked.c.app_key,
                ranked.c.app_status,
            )
            .where(ranked.c.rn <= per_user)
            .order_by(ranked.c.user_id, ranked.c.rn)
        )
        return [AccessRow(*row) for row in result.all()]

    async def list(
        self,
        *,
        email: str | None,
        email_exact: bool = False,
        status: UserStatus | None,
        app_id: uuid.UUID | None = None,
        role_id: uuid.UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[User], int]:
        conditions: list[ColumnElement[bool]] = []
        if email is not None and email_exact:
            conditions.append(User.email == email)  # CITEXT: case-insensitive, unique index
        elif email is not None:
            conditions.append(User.email.ilike(f"%{escape_like(email)}%", escape="\\"))
        if status is not None:
            conditions.append(User.status == status.value)
        if role_id is not None:
            conditions.append(
                select(RoleAssignment.id)
                .where(
                    RoleAssignment.user_id == User.id,
                    RoleAssignment.role_id == role_id,
                    active_assignment_clause(),
                )
                .correlate(User)
                .exists()
            )
        if app_id is not None:
            conditions.append(
                select(RoleAssignment.id)
                .join(Role, Role.id == RoleAssignment.role_id)
                .where(
                    RoleAssignment.user_id == User.id,
                    Role.app_id == app_id,
                    active_assignment_clause(),
                )
                .correlate(User)
                .exists()
            )
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
