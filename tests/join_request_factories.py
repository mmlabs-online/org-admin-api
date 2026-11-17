import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.join_requests.models import AppJoinRequest
from app.modules.rbac.models import App
from app.modules.users.models import User
from tests.auth_factories import make_login_app, make_member_role


async def make_approval_app(session: AsyncSession, key: str | None = None) -> App:
    """A login app with `join_policy = approval` and a `<key>:member` default role."""
    app = await make_login_app(
        session, key or f"app-{uuid.uuid4().hex[:8]}", join_policy="approval"
    )
    await make_member_role(session, app)
    return app


async def make_join_request(
    session: AsyncSession,
    app: App,
    user: User,
    status: str = "pending",
    decided_at: datetime | None = None,
) -> AppJoinRequest:
    """`decided_at` defaults to the database `now()` for a decided request."""
    row = AppJoinRequest(app_id=app.id, user_id=user.id, status=status, decided_at=decided_at)
    if status != "pending" and decided_at is None:
        row.decided_at = func.now()
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row
