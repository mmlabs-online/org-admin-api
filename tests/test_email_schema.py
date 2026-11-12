import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import EmailSendLog, EmailToken
from tests.auth_factories import sha256
from tests.rbac_factories import make_user


async def _insert_token(session: AsyncSession, user_id: uuid.UUID, **overrides: object) -> None:
    values: dict[str, object] = {
        "user_id": user_id,
        "purpose": "verify",
        "token_hash": sha256(uuid.uuid4().hex),
        "expires": "24 hours",
    } | overrides
    await session.execute(
        text(
            "INSERT INTO email_tokens (id, user_id, purpose, token_hash, expires_at) "
            "VALUES (gen_random_uuid(), :user_id, :purpose, :token_hash, "
            "now() + CAST(CAST(:expires AS text) AS interval))"
        ),
        values,
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"purpose": "login"}, "ck_email_tokens_purpose_valid"),
        ({"token_hash": b"short"}, "ck_email_tokens_token_hash_length"),
        ({"expires": "-1 second"}, "ck_email_tokens_expiry_after_creation"),
    ],
)
async def test_email_tokens_constraints(
    db_session: AsyncSession, overrides: dict[str, object], constraint: str
) -> None:
    user = await make_user(db_session)
    with pytest.raises(IntegrityError, match=constraint):
        await _insert_token(db_session, user.id, **overrides)


async def test_email_tokens_unique_hash_and_cascade(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    token_hash = sha256("same")
    await _insert_token(db_session, user.id, token_hash=token_hash)
    with pytest.raises(IntegrityError, match="uq_email_tokens_token_hash"):
        async with db_session.begin_nested():
            await _insert_token(db_session, user.id, token_hash=token_hash)
    row = (await db_session.execute(select(EmailToken.used_at, EmailToken.created_at))).one()
    assert row.used_at is None
    assert row.created_at is not None

    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    count = await db_session.scalar(select(func.count()).select_from(EmailToken))
    assert count == 0


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"email_hmac": b"short", "purpose": "verify"}, "ck_email_send_log_email_hmac_length"),
        ({"email_hmac": sha256("e"), "purpose": "login"}, "ck_email_send_log_purpose_valid"),
    ],
)
async def test_email_send_log_constraints(
    db_session: AsyncSession, values: dict[str, object], constraint: str
) -> None:
    db_session.add(EmailSendLog(**values))
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.flush()


async def test_email_send_log_default_sent_at(db_session: AsyncSession) -> None:
    db_session.add(EmailSendLog(email_hmac=sha256("e"), purpose="reset"))
    await db_session.flush()
    assert await db_session.scalar(select(EmailSendLog.sent_at == func.now())) is True
