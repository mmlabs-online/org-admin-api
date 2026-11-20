"""ADM-034: parallel first writes of one release on real Postgres (committed data, cleanup).

Two sessions write the same `(app, environment, version)` at the same time. The insert is
`ON CONFLICT DO NOTHING`, so the loser waits for the winner's commit and then reads the row:
exactly one creates it, the other is a no-op (identical body) or a replacement (different body),
and neither raises. `AppCredentialService.confirm` is stubbed (it takes the app lock, which
would serialise the two writers before the race) and the audit write is stubbed and counted, so
no append-only `audit_log` rows are left behind.
"""

import asyncio
import contextlib
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, func, select

from app.db.session import SessionLocal
from app.modules.app_credentials.service import AppCredentialService, AuthenticatedApp
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.rbac.models import App
from app.modules.releases.app_write import ReleaseAppWriteService
from app.modules.releases.models import AppRelease, ReleaseEnvironment
from app.modules.releases.repository import AppReleaseRepository
from app.modules.releases.write_schemas import ReleasePut
from tests.rbac_factories import make_app
from tests.test_login_concurrency import TIMEOUT

VERSION = "v9.9.9"
DIGEST = "sha256:" + "a" * 64


def _body(**over: Any) -> ReleasePut:
    data: dict[str, Any] = {
        "git_sha": "0123456789abcdef0123456789abcdef01234567",
        "images": [{"name": "mmlabs/api", "digest": DIGEST}],
        **over,
    }
    return ReleasePut.model_validate(data)


async def _race(
    monkeypatch: pytest.MonkeyPatch, bodies: list[ReleasePut]
) -> tuple[list[bool], list[str]]:
    audits: list[str] = []
    barrier = asyncio.Barrier(len(bodies))
    real_insert = AppReleaseRepository.insert_if_absent

    async def confirm(self: AppCredentialService, caller: AuthenticatedApp) -> None:
        return None

    async def record(self: AuditLogger, action: Any, **_: Any) -> None:
        audits.append(action.value)

    async def insert(self: AppReleaseRepository, **kwargs: Any) -> AppRelease | None:
        with contextlib.suppress(TimeoutError, asyncio.BrokenBarrierError):
            await asyncio.wait_for(barrier.wait(), timeout=2)
        row = await real_insert(self, **kwargs)
        if row is not None:
            await asyncio.sleep(0.3)  # keep the row uncommitted so the other writer must wait
        return row

    monkeypatch.setattr(AppCredentialService, "confirm", confirm)
    monkeypatch.setattr(AuditLogger, "record", record)
    monkeypatch.setattr(AppReleaseRepository, "insert_if_absent", insert)

    async with SessionLocal() as session:
        app = await make_app(session, f"race-{uuid.uuid4().hex[:8]}")
        await session.commit()
        app_id, app_key = app.id, app.key
    caller = AuthenticatedApp(app_id, "0" * 16, app_id, app_key, "releases")

    async def write(body: ReleasePut) -> bool:
        async with SessionLocal() as session:
            _, created = await ReleaseAppWriteService(session).put(
                caller, ReleaseEnvironment.PRODUCTION, VERSION, body, ctx=AuditContext.NONE
            )
            await session.commit()
            return created

    try:
        created = await asyncio.wait_for(asyncio.gather(*(write(b) for b in bodies)), TIMEOUT)
        async with SessionLocal() as session:
            count = await session.scalar(
                select(func.count()).select_from(AppRelease).where(AppRelease.app_id == app_id)
            )
        assert count == 1
        return list(created), audits
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(AppRelease).where(AppRelease.app_id == app_id))
            await session.execute(delete(App).where(App.id == app_id))
            await session.commit()


async def test_parallel_first_insert_one_creates_one_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    created, audits = await _race(monkeypatch, [_body(), _body()])
    assert sorted(created) == [False, True]
    assert audits == ["release.created"]


async def test_parallel_first_insert_different_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    created, audits = await _race(monkeypatch, [_body(), _body(has_migrations=True)])
    assert sorted(created) == [False, True]
    assert sorted(audits) == ["release.created", "release.updated"]
