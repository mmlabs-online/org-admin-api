import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User


async def test_status_check_rejects_unknown_value(db_session: AsyncSession) -> None:
    db_session.add(User(email="a@x.io", password_hash="h", status="banned"))
    with pytest.raises(IntegrityError, match="ck_users_status_valid"):
        await db_session.flush()


async def test_email_unique_is_case_insensitive(db_session: AsyncSession) -> None:
    db_session.add(User(email="a@x.io", password_hash="h"))
    await db_session.flush()
    db_session.add(User(email="A@X.IO", password_hash="h"))
    with pytest.raises(IntegrityError, match="uq_users_email"):
        await db_session.flush()


async def test_email_roundtrips_as_str(db_session: AsyncSession) -> None:
    db_session.add(User(email="Round@Trip.io", password_hash="h"))
    await db_session.flush()
    db_session.expunge_all()
    email = await db_session.scalar(select(User.email).where(User.email == "round@trip.io"))
    assert email == "Round@Trip.io"
    assert isinstance(email, str)
    assert await db_session.scalar(text("SELECT pg_typeof(email)::text FROM users")) == "citext"


def test_repr_has_no_password_hash() -> None:
    user = User(id=uuid.uuid4(), email="a@x.io", password_hash="$argon2id$secret-hash")
    assert "argon2" not in repr(user)
    assert "secret-hash" not in repr(user)
