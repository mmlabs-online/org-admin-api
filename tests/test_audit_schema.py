"""`audit_log` CHECKs, the append-only trigger (ADM-004) and `outcome` (ADM-004b); `alembic check`
cannot see CHECKs or triggers."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

INSERT = text(
    "INSERT INTO audit_log (id, actor_kind, actor_user_id, actor_label, action, target_type,"
    " target_id, app_key, changes, request_id) VALUES (:id, :actor_kind, :actor_user_id,"
    " :actor_label, :action, :target_type, :target_id, :app_key, CAST(:changes AS jsonb),"
    " :request_id)"
)


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "actor_kind": "user",
        "actor_user_id": uuid.uuid4(),
        "actor_label": None,
        "action": "organization.created",
        "target_type": "organization",
        "target_id": uuid.uuid4(),
        "app_key": None,
        "changes": '{"name": {"from": null, "to": "x"}}',
        "request_id": None,
    }
    row.update(overrides)
    return row


async def _insert(db_session: AsyncSession, **overrides: Any) -> uuid.UUID:
    row = _row(**overrides)
    await db_session.execute(INSERT, row)
    row_id: uuid.UUID = row["id"]
    return row_id


async def _refused(db_session: AsyncSession, **overrides: Any) -> None:
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await _insert(db_session, **overrides)


async def _append_only(db_session: AsyncSession, sql: str, **params: Any) -> None:
    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        async with db_session.begin_nested():
            await db_session.execute(text(sql), params)


async def test_valid_row_accepted_with_defaults(db_session: AsyncSession) -> None:
    row_id = await _insert(db_session)
    occurred = await db_session.scalar(
        text("SELECT occurred_at FROM audit_log WHERE id = :id"), {"id": row_id}
    )
    assert occurred is not None and occurred.tzinfo is not None


async def test_update_refused(db_session: AsyncSession) -> None:
    row_id = await _insert(db_session)
    await _append_only(
        db_session, "UPDATE audit_log SET action = 'user.enabled' WHERE id = :id", id=row_id
    )


async def test_delete_refused(db_session: AsyncSession) -> None:
    row_id = await _insert(db_session)
    await _append_only(db_session, "DELETE FROM audit_log WHERE id = :id", id=row_id)


async def test_truncate_refused(db_session: AsyncSession) -> None:
    await _insert(db_session)
    await _append_only(db_session, "TRUNCATE audit_log")


@pytest.mark.parametrize(
    "action", ["Organization.created", "organization", "org.created!", "a." + "b" * 64]
)
async def test_check_action_format(db_session: AsyncSession, action: str) -> None:
    await _refused(db_session, action=action)


@pytest.mark.parametrize(
    "overrides",
    [{"target_type": None}, {"target_id": None}],
)
async def test_check_target_pair(db_session: AsyncSession, overrides: dict[str, Any]) -> None:
    await _refused(db_session, **overrides)
    await _insert(db_session, target_type=None, target_id=None)  # both NULL is fine


@pytest.mark.parametrize("changes", ['["a"]', '"text"', "1", "null"])
async def test_check_changes_object(db_session: AsyncSession, changes: str) -> None:
    await _refused(db_session, changes=changes)


@pytest.mark.parametrize(
    "overrides",
    [
        {"actor_kind": "user", "actor_user_id": None},
        {"actor_kind": "system", "actor_user_id": None, "actor_label": None},
        {"actor_kind": "system", "actor_label": "system:cli"},  # system with a user id
        {"actor_kind": "anonymous", "actor_user_id": uuid.uuid4()},
        {"actor_kind": "robot", "actor_user_id": None},
        {"actor_kind": "system", "actor_user_id": None, "actor_label": "x" * 65},
        {"app_key": "Bad Key"},
    ],
)
async def test_check_actor_consistency(db_session: AsyncSession, overrides: dict[str, Any]) -> None:
    await _refused(db_session, **overrides)
    await _insert(db_session, actor_kind="system", actor_user_id=None, actor_label="system:cli")
    await _insert(db_session, actor_kind="anonymous", actor_user_id=None)


@pytest.mark.parametrize("request_id", ["", "a" * 65, "has space", "<x>"])
async def test_check_request_id_format(db_session: AsyncSession, request_id: str) -> None:
    await _refused(db_session, request_id=request_id)
    await _insert(db_session, request_id=str(uuid.uuid4()))


async def test_outcome_default_and_check(db_session: AsyncSession) -> None:
    row_id = await _insert(db_session)  # INSERT without `outcome`
    outcome = await db_session.scalar(
        text("SELECT outcome FROM audit_log WHERE id = :id"), {"id": row_id}
    )
    assert outcome == "success"
    for value in ("failure", "denied"):
        await db_session.execute(
            text(
                "INSERT INTO audit_log (id, actor_kind, action, outcome) "
                "VALUES (gen_random_uuid(), 'anonymous', 'auth.login_failed', :o)"
            ),
            {"o": value},
        )
    with pytest.raises(IntegrityError, match="ck_audit_log_outcome_valid"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO audit_log (id, actor_kind, action, outcome) "
                    "VALUES (gen_random_uuid(), 'anonymous', 'auth.login_failed', 'other')"
                )
            )


async def test_outcome_partial_index_used(db_session: AsyncSession) -> None:
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = (
        await db_session.execute(
            text(
                "EXPLAIN SELECT id FROM audit_log WHERE outcome = 'denied' "
                "ORDER BY occurred_at DESC, id DESC LIMIT 50"
            )
        )
    ).scalars()
    assert "ix_audit_log_outcome" in "\n".join(plan)
