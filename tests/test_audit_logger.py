"""`AuditLogger.record` (ADM-004): forbidden keys, JSON coercion, request context hygiene."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin_auth.service import AdminPrincipal
from app.modules.audit.actions import AuditAction, AuditTarget
from app.modules.audit.context import AuditContext, EndUser
from app.modules.audit.diff import change
from app.modules.audit.models import AuditLog
from app.modules.audit.service import AuditLogger
from app.modules.organizations.models import OrganizationStatus
from app.modules.rbac.actor import SYSTEM_CLI

PRINCIPAL = AdminPrincipal(uuid.uuid4(), frozenset({"admin:*"}), "jti")


async def _only_row(db_session: AsyncSession) -> AuditLog:
    (row,) = await db_session.scalars(select(AuditLog))
    return row


@pytest.mark.parametrize(
    "key",
    ["password_hash", "token", "access_token", "password", "cookie", "email", "Secret", "x_EMAIL"],
)
async def test_forbidden_change_key_rejected(db_session: AsyncSession, key: str) -> None:
    statements: list[str] = []
    conn = db_session.bind.sync_connection  # type: ignore[union-attr]
    assert conn is not None

    def _record(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(conn, "before_cursor_execute", _record)
    try:
        with pytest.raises(ValueError, match="forbidden audit change key"):
            await AuditLogger(db_session).record(
                AuditAction.USER_DISABLED,
                actor=PRINCIPAL,
                ctx=AuditContext.NONE,
                changes={key: change(None, "x")},
            )
    finally:
        event.remove(conn, "before_cursor_execute", _record)
    assert statements == []  # rejected before any SQL


async def test_values_json_coerced(db_session: AsyncSession) -> None:
    role_id = uuid.uuid4()
    when = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    await AuditLogger(db_session).record(
        AuditAction.ROLE_ASSIGNMENT_GRANTED,
        actor=SYSTEM_CLI,
        ctx=AuditContext.NONE,
        target=(AuditTarget.ROLE_ASSIGNMENT, uuid.uuid4()),
        changes={
            "role_id": change(None, role_id),
            "expires_at": change(None, when),
            "status": change(OrganizationStatus.ACTIVE, OrganizationStatus.SUSPENDED),
            "origins": change(("https://a.mmlabs.online",), []),
        },
    )
    row = await _only_row(db_session)
    assert row.changes == {
        "role_id": {"from": None, "to": str(role_id)},
        "expires_at": {"from": None, "to": when.isoformat()},
        "status": {"from": "active", "to": "suspended"},
        "origins": {"from": ["https://a.mmlabs.online"], "to": []},
    }
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("system", None, "system:cli")
    assert (row.ip, row.user_agent, row.request_id) == (None, None, None)


async def test_empty_changes_stored_as_null(db_session: AsyncSession) -> None:
    user_id = uuid.uuid4()
    await AuditLogger(db_session).record(
        AuditAction.APP_JOINED, actor=EndUser(user_id), ctx=AuditContext.NONE, changes={}
    )
    row = await _only_row(db_session)
    assert (row.actor_kind, row.actor_user_id, row.changes) == ("user", user_id, None)


async def test_user_agent_truncated(db_session: AsyncSession) -> None:
    ctx = AuditContext(ip="2001:db8::1", user_agent="u" * 600, request_id="r-1")
    await AuditLogger(db_session).record(AuditAction.USER_ENABLED, actor=PRINCIPAL, ctx=ctx)
    row = await _only_row(db_session)
    assert row.user_agent == "u" * 512
    assert (str(row.ip), row.request_id, row.actor_user_id) == (
        "2001:db8::1",
        "r-1",
        PRINCIPAL.user_id,
    )


@pytest.mark.parametrize("ip", ["unknown", "", "999.1.1.1", "testclient"])
async def test_invalid_ip_dropped(db_session: AsyncSession, ip: str) -> None:
    ctx = AuditContext(ip=ip)
    await AuditLogger(db_session).record(AuditAction.USER_ENABLED, actor=PRINCIPAL, ctx=ctx)
    assert (await _only_row(db_session)).ip is None
