"""USR-36: a bulk assign and a single assign on the same app (ADR-0029): committed data, every
request on its own connection, serialized by `lock_app`."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select, text

from app.db.session import SessionLocal
from app.main import create_app
from app.modules.admin_auth.deps import current_admin
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.audit.models import AuditLog
from app.modules.rbac import repository as rbac_repository
from app.modules.rbac.models import App, RoleAssignment
from app.modules.users.models import User
from tests.rbac_factories import make_app, make_role, make_user

KEY = "batchrace"


@dataclass
class Race:
    role_id: uuid.UUID
    actor_id: uuid.UUID
    user_ids: list[uuid.UUID]


@pytest.fixture
async def race() -> AsyncIterator[Race]:
    async with SessionLocal() as session:
        app = await make_app(session, KEY)
        role = await make_role(session, app, f"{KEY}:viewer")
        actor = await make_user(session)
        users = [await make_user(session) for _ in range(21)]
        race_ = Race(role.id, actor.id, [u.id for u in users])
        await session.commit()
    try:
        yield race_
    finally:
        async with SessionLocal() as session:
            ids = [race_.actor_id, *race_.user_ids]
            # The append-only `audit_log` rows stay; every query is scoped by this run's user ids.
            await session.execute(
                delete(RoleAssignment).where(RoleAssignment.role_id == race_.role_id)
            )
            await session.execute(delete(App).where(App.key == KEY))  # cascades roles, assignments
            await session.execute(delete(User).where(User.id.in_(ids)))
            await session.commit()


def _app(race: Race) -> FastAPI:
    app = create_app()
    principal = AdminPrincipal(race.actor_id, frozenset({"admin:*"}), "test")
    app.dependency_overrides[current_admin] = lambda: principal
    return app


async def _batch(app: FastAPI, race: Race, users: list[uuid.UUID]) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(
            f"/api/v1/apps/{KEY}/roles/{race.role_id}/assignment-batches",
            json={"user_ids": [str(u) for u in users]},
        )


async def _single(app: FastAPI, race: Race, user: uuid.UUID) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(
            f"/api/v1/users/{user}/role-assignments", json={"role_id": str(race.role_id)}
        )


async def _count(race: Race, user: uuid.UUID) -> tuple[int, int]:
    async with SessionLocal() as session:
        assignments = await session.scalar(
            select(func.count()).select_from(RoleAssignment).where(RoleAssignment.user_id == user)
        )
        rows = await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "role_assignment.granted", AuditLog.app_key == KEY)
            .where(AuditLog.changes["user_id"]["to"].astext == str(user))
        )
    return assignments or 0, rows or 0


async def test_batch_and_single_assign_same_user_one_wins(race: Race) -> None:
    app = _app(race)
    shared = race.user_ids[0]
    batch, single = await asyncio.wait_for(
        asyncio.gather(_batch(app, race, race.user_ids[:20]), _single(app, race, shared)),
        timeout=30,
    )
    assert batch.status_code == 200, batch.text
    mine = next(r for r in batch.json()["results"] if r["user_id"] == str(shared))
    assert (single.status_code, mine["status"]) in {(201, "unchanged"), (409, "created")}
    if single.status_code == 409:
        assert single.json()["error"]["details"] == [{"reason": "assignment_exists"}]
    assert await _count(race, shared) == (1, 1)
    others = [r for r in batch.json()["results"] if r["user_id"] != str(shared)]
    assert {r["status"] for r in others} == {"created"}


async def test_batch_and_single_assign_other_users_both_win(race: Race) -> None:
    app = _app(race)
    batch, single = await asyncio.wait_for(
        asyncio.gather(
            _batch(app, race, race.user_ids[:20]), _single(app, race, race.user_ids[20])
        ),
        timeout=30,
    )
    assert batch.status_code == 200 and single.status_code == 201
    assert {r["status"] for r in batch.json()["results"]} == {"created"}
    assert await _count(race, race.user_ids[20]) == (1, 1)
    assert await _count(race, race.user_ids[5]) == (1, 1)


async def test_batch_lock_timeout_409_nothing_written(
    race: Race, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rbac_repository, "ASSIGNMENT_BATCH_LOCK_TIMEOUT", "200ms")
    app = _app(race)
    async with SessionLocal() as holder:
        await holder.execute(
            text("SELECT 1 FROM apps WHERE key = :k FOR NO KEY UPDATE"), {"k": KEY}
        )
        resp = await asyncio.wait_for(_batch(app, race, race.user_ids[:3]), timeout=30)
        await holder.rollback()
    assert resp.status_code == 409, resp.text
    error = resp.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"] == [{"reason": "changed_concurrently"}]
    for user in race.user_ids[:3]:
        assert await _count(race, user) == (0, 0)
