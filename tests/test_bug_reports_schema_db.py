"""ADM-028: CHECK constraints and FK behaviour of `bug_reports` by raw insert."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.auth_factories import make_login_app, make_login_user

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def _insert(db: AsyncSession, app_id: uuid.UUID, user_id: uuid.UUID, **cols: Any) -> None:
    values: dict[str, Any] = {"description": "d", **cols}
    names = ["id", "app_id", "user_id", *values]
    sql = (
        f"INSERT INTO bug_reports ({', '.join(names)}) VALUES ({', '.join(':' + n for n in names)})"  # noqa: S608
    )
    await db.execute(
        text(sql), {"id": uuid.uuid4(), "app_id": app_id, "user_id": user_id, **values}
    )


@pytest.fixture
async def ids(db_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    app = await make_login_app(db_session, "ricotdin")
    user = await make_login_user(db_session)
    return app.id, user.id


async def _refused(
    db: AsyncSession, ids: tuple[uuid.UUID, uuid.UUID], name: str, **cols: Any
) -> None:
    with pytest.raises(IntegrityError) as exc:
        async with db.begin_nested():
            await _insert(db, *ids, **cols)
    assert name in str(exc.value)


async def test_valid_open_and_done_rows(
    db_session: AsyncSession, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    await _insert(db_session, *ids)
    await _insert(db_session, *ids, status="done", handled_at=NOW)


async def test_status_check(db_session: AsyncSession, ids: tuple[uuid.UUID, uuid.UUID]) -> None:
    await _refused(db_session, ids, "ck_bug_reports_status_valid", status="closed")


async def test_handled_iff_done_check(
    db_session: AsyncSession, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    await _refused(db_session, ids, "ck_bug_reports_handled_iff_done", status="done")
    await _refused(db_session, ids, "ck_bug_reports_handled_iff_done", handled_at=NOW)


async def test_handler_only_when_done_check(
    db_session: AsyncSession, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    await _refused(
        db_session,
        ids,
        "ck_bug_reports_handler_only_when_done",
        handled_by=ids[1],
    )


async def test_description_length_check(
    db_session: AsyncSession, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    await _refused(db_session, ids, "ck_bug_reports_description_length", description="")
    await _refused(db_session, ids, "ck_bug_reports_description_length", description="x" * 5001)
    await _insert(db_session, *ids, description="x" * 5000)


@pytest.mark.parametrize(
    "url", ["https://h/a?q=1", "https://h/a#f", "ftp://h/a", "https://u@h/a", "https://h", "/a"]
)
async def test_page_url_reduced_check(
    db_session: AsyncSession, ids: tuple[uuid.UUID, uuid.UUID], url: str
) -> None:
    await _refused(db_session, ids, "ck_bug_reports_page_url_reduced", page_url=url)


async def test_page_url_reduced_accepts(
    db_session: AsyncSession, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    await _insert(db_session, *ids, page_url="https://h.example:8443/đường/x")


async def test_app_delete_restricted_by_report(
    db_session: AsyncSession, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    await _insert(db_session, *ids)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(text("DELETE FROM apps WHERE id = :i"), {"i": ids[0]})
