"""USR-06b schema: `org_invitations` constraints and the widened `email_send_log` purpose CHECK."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import EmailSendLog
from app.modules.invitations.models import OrgInvitation
from app.modules.organizations.models import Organization
from tests.rbac_factories import make_app, make_org, make_role, make_user


def _hash(seed: str) -> bytes:
    return hashlib.sha256(seed.encode()).digest()


async def _invitation(
    db_session: AsyncSession, org: Organization, role_id: object, app_id: object, **kw: object
) -> OrgInvitation:
    fields: dict[str, object] = {
        "org_id": org.id,
        "app_id": app_id,
        "role_id": role_id,
        "email_hmac": _hash("email"),
        "token_hash": _hash(f"token-{datetime.now(UTC).timestamp()}"),
        "expires_at": datetime.now(UTC) + timedelta(days=1),
        **kw,
    }
    invitation = OrgInvitation(**fields)
    db_session.add(invitation)
    await db_session.flush()
    return invitation


async def _base(db_session: AsyncSession) -> tuple[Organization, object, object]:
    app = await make_app(db_session, "calechip", org_mode="required")
    role = await make_role(db_session, app, "calechip:viewer")
    return await make_org(db_session), role.id, app.id


async def _refused(db_session: AsyncSession, match: str, **kw: object) -> None:
    org, role_id, app_id = await _base(db_session)
    with pytest.raises(IntegrityError, match=match):
        async with db_session.begin_nested():
            await _invitation(db_session, org, role_id, app_id, **kw)


async def test_invitations_constraints(db_session: AsyncSession) -> None:
    await _refused(db_session, "ck_org_invitations_token_hash_length", token_hash=b"short")


async def test_email_hmac_length(db_session: AsyncSession) -> None:
    await _refused(db_session, "ck_org_invitations_email_hmac_length", email_hmac=b"short")


async def test_expiry_after_creation(db_session: AsyncSession) -> None:
    await _refused(
        db_session,
        "ck_org_invitations_expiry_after_creation",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )


async def test_accepted_and_revoked_refused(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await _refused(
        db_session, "ck_org_invitations_not_accepted_and_revoked", accepted_at=now, revoked_at=now
    )


async def test_token_hash_unique(db_session: AsyncSession) -> None:
    org, role_id, app_id = await _base(db_session)
    await _invitation(db_session, org, role_id, app_id, token_hash=_hash("t"))
    with pytest.raises(IntegrityError, match="uq_org_invitations_token_hash"):
        async with db_session.begin_nested():
            await _invitation(
                db_session, org, role_id, app_id, token_hash=_hash("t"), email_hmac=_hash("other")
            )


async def test_partial_unique_pending(db_session: AsyncSession) -> None:
    org, role_id, app_id = await _base(db_session)
    first = await _invitation(db_session, org, role_id, app_id, token_hash=_hash("a"))
    with pytest.raises(IntegrityError, match="uq_org_invitations_pending"):
        async with db_session.begin_nested():
            await _invitation(db_session, org, role_id, app_id, token_hash=_hash("b"))
    first.revoked_at = datetime.now(UTC)  # once revoked (or accepted) another may be pending
    await db_session.flush()
    await _invitation(db_session, org, role_id, app_id, token_hash=_hash("c"))
    await _invitation(
        db_session, org, role_id, app_id, token_hash=_hash("d"), email_hmac=_hash("x")
    )


async def test_org_delete_cascades(db_session: AsyncSession) -> None:
    org, role_id, app_id = await _base(db_session)
    await _invitation(db_session, org, role_id, app_id)
    await db_session.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org.id})
    assert list(await db_session.scalars(select(OrgInvitation))) == []


async def test_role_delete_cascades(db_session: AsyncSession) -> None:
    org, role_id, app_id = await _base(db_session)
    await _invitation(db_session, org, role_id, app_id)
    await db_session.execute(text("DELETE FROM roles WHERE id = :id"), {"id": role_id})
    assert list(await db_session.scalars(select(OrgInvitation))) == []


async def test_role_must_belong_to_the_invitation_app(db_session: AsyncSession) -> None:
    org, role_id, _ = await _base(db_session)
    other = await make_app(db_session, "other")
    with pytest.raises(IntegrityError, match="fk_org_invitations_role_same_app"):
        async with db_session.begin_nested():
            await _invitation(db_session, org, role_id, other.id)


async def test_inviter_delete_sets_null(db_session: AsyncSession) -> None:
    org, role_id, app_id = await _base(db_session)
    user = await make_user(db_session)
    invitation = await _invitation(db_session, org, role_id, app_id, invited_by=user.id)
    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    await db_session.refresh(invitation)
    assert invitation.invited_by is None


async def test_send_log_purpose_check_text(db_session: AsyncSession) -> None:
    definition = await db_session.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_email_send_log_purpose_valid'"
        )
    )
    assert definition is not None
    assert "'verify'" in definition and "'reset'" in definition and "'invite'" in definition
    # The model declares the same CHECK text (Alembic does not compare CHECKs).
    model_checks = {str(c.sqltext) for c in EmailSendLog.__table_args__ if hasattr(c, "sqltext")}
    assert "purpose IN ('verify', 'reset', 'invite')" in model_checks
    db_session.add(EmailSendLog(email_hmac=_hash("e"), purpose="invite"))
    await db_session.flush()
    with pytest.raises(IntegrityError, match="ck_email_send_log_purpose_valid"):
        async with db_session.begin_nested():
            db_session.add(EmailSendLog(email_hmac=_hash("e"), purpose="other"))
            await db_session.flush()
