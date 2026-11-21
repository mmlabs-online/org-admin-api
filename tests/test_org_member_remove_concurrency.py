"""Two owners removing each other at once leave one owner (USR-30, ADR-0026): committed data,
every request on its own connection, serialized by `lock_app`."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, select, text

from app.db.session import SessionLocal
from app.main import create_app
from app.modules.organizations.models import Organization
from app.modules.rbac.models import App
from app.modules.sessions.models import AccountSession, DeviceSession
from app.modules.users.models import User
from tests.invitation_factories import Person
from tests.org_member_factories import RemoveEnv, member, remove_body, remove_url, setup_removal


@dataclass
class Race:
    renv: RemoveEnv
    first: Person
    second: Person


@pytest.fixture
async def race() -> AsyncIterator[Race]:
    async with SessionLocal() as session:
        renv = await setup_removal(session)
        second = await member(session, renv, renv.env.owner)
        await session.commit()
    race_ = Race(renv, renv.owner, second)
    try:
        yield race_
    finally:
        async with SessionLocal() as session:
            user_ids = [renv.owner.user.id, second.user.id]
            device_ids = (
                await session.scalars(
                    select(AccountSession.device_session_id).where(
                        AccountSession.user_id.in_(user_ids)
                    )
                )
            ).all()
            await session.execute(
                delete(AccountSession).where(AccountSession.user_id.in_(user_ids))
            )
            if device_ids:
                await session.execute(delete(DeviceSession).where(DeviceSession.id.in_(device_ids)))
            await session.execute(delete(User).where(User.id.in_(user_ids)))
            await session.execute(delete(Organization).where(Organization.id == renv.org.id))
            await session.execute(delete(App).where(App.key == "calechip"))
            await session.commit()


async def test_two_owners_remove_each_other_one_wins(race: Race) -> None:
    app = create_app()

    async def remove(caller: Person, target: Person) -> Response:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                remove_url(race.renv.org.id, target.user.id),
                json=remove_body(caller),
                headers=caller.headers(),
            )

    responses = await asyncio.wait_for(
        asyncio.gather(remove(race.first, race.second), remove(race.second, race.first)),
        timeout=20,
    )
    # The loser queues on the app lock; when it runs, the winner has already removed its caller,
    # so it is refused as a non-member (403), not by the last-owner rule (409).
    assert sorted(r.status_code for r in responses) == [204, 403]
    loser = next(r for r in responses if r.status_code == 403)
    assert loser.json()["error"]["code"] == "org_access_denied"
    async with SessionLocal() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM role_assignments WHERE role_id = :r AND org_id = :o"),
            {"r": race.renv.env.owner.id, "o": race.renv.org.id},
        )
    assert count == 1
