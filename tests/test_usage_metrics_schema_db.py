"""ADM-039: CHECK, unique and FK behaviour of `usage_metrics` by raw insert."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.rbac_factories import make_app

GOOD: dict[str, Any] = {"key": "chat-turns", "display_name": "Chat", "unit": "count"}


async def _insert(db: AsyncSession, app_id: uuid.UUID, **cols: Any) -> None:
    values: dict[str, Any] = {"default_limit": 5, **GOOD, **cols}
    names = ["id", "app_id", *values]
    sql = (
        f"INSERT INTO usage_metrics ({', '.join(names)}) "  # noqa: S608
        f"VALUES ({', '.join(':' + n for n in names)})"
    )
    await db.execute(text(sql), {"id": uuid.uuid4(), "app_id": app_id, **values})


@pytest.fixture
async def app_id(db_session: AsyncSession) -> uuid.UUID:
    return (await make_app(db_session, "metricapp")).id


@pytest.mark.parametrize(
    ("name", "cols"),
    [
        ("key_format", {"key": "Bad"}),
        ("key_format", {"key": "a"}),
        ("unit_format", {"unit": "1x"}),
        ("display_name_length", {"display_name": ""}),
        ("default_limit_range", {"default_limit": -1}),
        ("default_limit_range", {"default_limit": 2**53}),
        ("period_valid", {"period": "year"}),
        ("status_valid", {"status": "deleted"}),
    ],
)
async def test_check_constraints(
    db_session: AsyncSession, app_id: uuid.UUID, name: str, cols: dict[str, Any]
) -> None:
    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            await _insert(db_session, app_id, **cols)
    assert f"ck_usage_metrics_{name}" in str(exc.value)


async def test_unique_app_key(db_session: AsyncSession, app_id: uuid.UUID) -> None:
    await _insert(db_session, app_id)
    other = (await make_app(db_session, "otherapp")).id
    await _insert(db_session, other)  # same key, another app
    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            await _insert(db_session, app_id)
    assert "uq_usage_metrics_app_id_key" in str(exc.value)


async def test_server_defaults(db_session: AsyncSession, app_id: uuid.UUID) -> None:
    await _insert(db_session, app_id, default_limit=0)
    row = (await db_session.execute(text("SELECT period, status FROM usage_metrics"))).one()
    assert tuple(row) == ("week", "active")


async def test_app_fk_restrict(db_session: AsyncSession, app_id: uuid.UUID) -> None:
    await _insert(db_session, app_id)
    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            await db_session.execute(text("DELETE FROM apps WHERE id = :i"), {"i": app_id})
    assert "fk_usage_metrics_app_id_apps" in str(exc.value)
