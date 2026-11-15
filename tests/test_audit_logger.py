"""`AuditLogger` (ADM-004, ADM-004b): forbidden keys, JSON coercion, request context hygiene,
anonymous actors and the failure-path writes."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AdminWildcardRequiredError
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.audit import service as audit_service
from app.modules.audit.actions import AuditAction, AuditOutcome, AuditTarget
from app.modules.audit.context import ANONYMOUS, AuditContext, EndUser
from app.modules.audit.diff import change
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditLogger
from app.modules.organizations.models import Organization, OrganizationStatus
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


# --- ADM-004b: anonymous actor, flat changes, failure paths, hardening -----------------


def _logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Alembic's `fileConfig` (session fixture) disables loggers created before it ran."""
    monkeypatch.setattr(audit_service.logger, "disabled", False)
    caplog.set_level(logging.INFO)


async def _count(db_session: AsyncSession) -> int:
    return await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0


async def test_anonymous_actor_row(db_session: AsyncSession) -> None:
    await AuditLogger(db_session).record(
        AuditAction.AUTH_LOGGED_OUT_ALL,
        actor=ANONYMOUS,
        ctx=AuditContext.NONE,
        target=(AuditTarget.DEVICE_SESSION, uuid.uuid4()),
        changes={"accounts_revoked": 2},
    )
    row = await _only_row(db_session)
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("anonymous", None, None)
    assert (row.changes, row.outcome) == ({"accounts_revoked": 2}, "success")


async def test_record_failure_rolls_back_pending_work(db_session: AsyncSession) -> None:
    await db_session.commit()
    org = Organization(slug=f"org-{uuid.uuid4().hex[:8]}", name="Pending")
    db_session.add(org)
    await db_session.flush()
    slug = org.slug
    await AuditLogger(db_session).record_failure(
        AuditAction.AUTH_LOGIN_FAILED,
        outcome=AuditOutcome.FAILURE,
        actor=ANONYMOUS,
        ctx=AuditContext.NONE,
        changes={"reason": "unknown_account"},
    )
    assert await db_session.scalar(select(Organization.id).where(Organization.slug == slug)) is None
    row = await _only_row(db_session)
    assert (row.action, row.outcome) == ("auth.login_failed", "failure")


async def test_record_failure_swallows_and_logs(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _logs(monkeypatch, caplog)

    async def boom(self: AuditRepository, row: AuditLog) -> None:
        raise RuntimeError("insert failed")

    monkeypatch.setattr(AuditRepository, "add", boom)
    await AuditLogger(db_session).record_failure(  # no exception
        AuditAction.AUTH_LOGIN_FAILED,
        outcome=AuditOutcome.FAILURE,
        actor=ANONYMOUS,
        ctx=AuditContext.NONE,
    )
    assert (
        "audit_write_failed action=auth.login_failed outcome=failure error=RuntimeError"
        in caplog.text
    )
    assert await _count(db_session) == 0
    with pytest.raises(ValueError, match="failure or denied"):
        await AuditLogger(db_session).record_failure(
            AuditAction.AUTH_LOGIN_FAILED,
            outcome=AuditOutcome.SUCCESS,
            actor=ANONYMOUS,
            ctx=AuditContext.NONE,
        )


async def test_record_failure_cancelled_propagates(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def cancelled(self: AuditRepository, row: AuditLog) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(AuditRepository, "add", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await AuditLogger(db_session).record_failure(
            AuditAction.AUTH_LOGIN_FAILED,
            outcome=AuditOutcome.FAILURE,
            actor=ANONYMOUS,
            ctx=AuditContext.NONE,
        )


async def test_record_in_savepoint_keeps_outer_work(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _logs(monkeypatch, caplog)
    org = Organization(slug=f"org-{uuid.uuid4().hex[:8]}", name="Outer")
    db_session.add(org)
    await db_session.flush()
    org_id = org.id
    logger = AuditLogger(db_session)
    target = (AuditTarget.DEVICE_SESSION, uuid.uuid4())
    await logger.record_in_savepoint(
        AuditAction.DEVICE_SESSION_REUSE_REVOKED,
        actor=ANONYMOUS,
        ctx=AuditContext.NONE,
        target=target,
        changes={"accounts_revoked": 1, "during_login": False},
    )
    assert await _count(db_session) == 1

    real_add = AuditRepository.add

    async def flush_then_fail(self: AuditRepository, row: AuditLog) -> None:
        await real_add(self, row)
        raise RuntimeError("after flush")

    monkeypatch.setattr(AuditRepository, "add", flush_then_fail)
    await logger.record_in_savepoint(
        AuditAction.DEVICE_SESSION_REUSE_REVOKED,
        actor=ANONYMOUS,
        ctx=AuditContext.NONE,
        target=target,
    )
    assert (
        "audit_write_failed action=device_session.reuse_revoked outcome=success error=RuntimeError"
        in caplog.text
    )
    assert await _count(db_session) == 1  # the failed row's savepoint was rolled back
    assert await db_session.scalar(select(Organization.id).where(Organization.id == org_id))


async def test_record_denied_skips_system_actor(db_session: AsyncSession) -> None:
    org = Organization(slug=f"org-{uuid.uuid4().hex[:8]}", name="Kept")
    db_session.add(org)
    await db_session.flush()
    org_id = org.id
    await AuditLogger(db_session).record_denied(
        AuditAction.APP_UPDATED,
        exc=AdminWildcardRequiredError(),
        actor=SYSTEM_CLI,
        ctx=AuditContext.NONE,
        app_key="admin",
    )
    assert await _count(db_session) == 0
    assert await db_session.scalar(select(Organization.id).where(Organization.id == org_id))


async def test_allowed_keys_only_for_retention_ints(db_session: AsyncSession) -> None:
    logger = AuditLogger(db_session)
    await logger.record(
        AuditAction.EMAIL_RETENTION_PURGED,
        actor=SYSTEM_CLI,
        ctx=AuditContext.NONE,
        changes={"email_send_log": change(None, 3), "email_tokens": change(None, 0)},
    )
    assert (await _only_row(db_session)).changes == {
        "email_send_log": {"from": None, "to": 3},
        "email_tokens": {"from": None, "to": 0},
    }
    bad: list[tuple[AuditAction, Any]] = [
        (AuditAction.APP_UPDATED, change(None, 3)),  # wrong action
        (AuditAction.EMAIL_RETENTION_PURGED, change(None, "3")),  # not an int
        (AuditAction.EMAIL_RETENTION_PURGED, change(None, True)),  # bool is not a count
        (AuditAction.EMAIL_RETENTION_PURGED, change(1, 3)),  # not a summary
        (AuditAction.EMAIL_RETENTION_PURGED, 3),  # flat value
    ]
    for action, value in bad:
        with pytest.raises(ValueError, match="forbidden audit change key"):
            await logger.record(
                action, actor=SYSTEM_CLI, ctx=AuditContext.NONE, changes={"email_tokens": value}
            )


@pytest.mark.parametrize("ip", ["fe80::1%eth0", "fe80::1%1"])
async def test_scoped_ipv6_dropped(db_session: AsyncSession, ip: str) -> None:
    await AuditLogger(db_session).record(
        AuditAction.USER_ENABLED, actor=PRINCIPAL, ctx=AuditContext(ip=ip)
    )
    assert (await _only_row(db_session)).ip is None


async def test_ipv4_mapped_normalised(db_session: AsyncSession) -> None:
    await AuditLogger(db_session).record(
        AuditAction.USER_ENABLED, actor=PRINCIPAL, ctx=AuditContext(ip="::ffff:192.0.2.7")
    )
    assert str((await _only_row(db_session)).ip) == "192.0.2.7"


@pytest.mark.parametrize(
    ("ua", "stored"),
    [
        ("a\x00b\x1fc\x7fd\te", "abcde"),
        ("\x00\x01\x7f", None),
        ("\x00" + "u" * 600, "u" * 512),
        ("a\x85b\x9bc\x80", "abc"),
        ("\x9f", None),
    ],
)
async def test_user_agent_control_chars_stripped(
    db_session: AsyncSession, ua: str, stored: str | None
) -> None:
    await AuditLogger(db_session).record(
        AuditAction.USER_ENABLED, actor=PRINCIPAL, ctx=AuditContext(user_agent=ua)
    )
    assert (await _only_row(db_session)).user_agent == stored
