"""ADM-036: deploy report racing the app write on real Postgres (committed data, cleanup).

Two sessions touch the same `(app, environment, version)` at once. The insert is
`ON CONFLICT DO NOTHING`, so the loser waits for the winner's commit, then reads the row `FOR
UPDATE` and decides on it. Credential checks are skipped and the audit write is stubbed and
counted, so no append-only `audit_log` rows are left behind.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.modules.app_credentials.service import AppCredentialService, AuthenticatedApp
from app.modules.audit.context import AuditContext
from app.modules.audit.service import AuditLogger
from app.modules.deployer_credentials.service import AuthenticatedDeployer
from app.modules.rbac.models import App
from app.modules.releases.app_write import ReleaseAppWriteService
from app.modules.releases.deployment import ReleaseDeploymentService
from app.modules.releases.models import AppRelease, ReleaseEnvironment
from app.modules.releases.repository import AppReleaseRepository
from app.modules.releases.write_schemas import ReleaseDeploymentReport, ReleasePut
from tests.rbac_factories import make_app
from tests.test_login_concurrency import TIMEOUT

VERSION = "v9.9.9"
RUN = "https://github.com/mmlabs-online/deployment/actions/runs"
ENV = ReleaseEnvironment.PRODUCTION

Op = Callable[[uuid.UUID, str], Awaitable[bool]]


def _report(url: str = f"{RUN}/1") -> ReleaseDeploymentReport:
    return ReleaseDeploymentReport(
        status="deployed", deploy_run_url=url, deployed_at=datetime.now(UTC)
    )


def _put() -> ReleasePut:
    return ReleasePut.model_validate(
        {
            "git_sha": "0123456789abcdef0123456789abcdef01234567",
            "images": [{"name": "mmlabs/api", "digest": "sha256:" + "a" * 64}],
        }
    )


async def _report_op(app_id: uuid.UUID, key: str) -> bool:
    caller = AuthenticatedDeployer(uuid.uuid4(), "0" * 16)
    async with SessionLocal() as session:
        _, created = await ReleaseDeploymentService(session).report(
            caller, key, ENV, VERSION, _report(), ctx=AuditContext.NONE
        )
        await session.commit()
        return created


async def _put_op(app_id: uuid.UUID, key: str) -> bool:
    caller = AuthenticatedApp(app_id, "0" * 16, app_id, key, "releases")
    async with SessionLocal() as session:
        _, created = await ReleaseAppWriteService(session).put(
            caller, ENV, VERSION, _put(), ctx=AuditContext.NONE
        )
        await session.commit()
        return created


async def _race(
    monkeypatch: pytest.MonkeyPatch, first: Op, second: Op
) -> tuple[list[bool], list[str], AppRelease]:
    """`first` inserts and holds its transaction open; `second` starts while it is held."""
    audits: list[str] = []
    inserted = asyncio.Event()
    real_insert = AppReleaseRepository.insert_if_absent

    async def confirm(self: AppCredentialService, caller: AuthenticatedApp) -> None:
        return None

    async def record(self: AuditLogger, action: Any, **_: Any) -> None:
        audits.append(action.value)

    async def insert(self: AppReleaseRepository, **kwargs: Any) -> AppRelease | None:
        row = await real_insert(self, **kwargs)
        if row is not None:
            inserted.set()
            await asyncio.sleep(0.3)  # keep the row uncommitted so the other writer must wait
        return row

    monkeypatch.setattr(AppCredentialService, "confirm", confirm)
    monkeypatch.setattr(AuditLogger, "record", record)
    monkeypatch.setattr(AppReleaseRepository, "insert_if_absent", insert)

    async with SessionLocal() as session:
        app = await make_app(session, f"race-{uuid.uuid4().hex[:8]}")
        await session.commit()
        app_id, key = app.id, app.key

    async def delayed() -> bool:
        await asyncio.wait_for(inserted.wait(), timeout=2)
        return await second(app_id, key)

    try:
        created = await asyncio.wait_for(asyncio.gather(first(app_id, key), delayed()), TIMEOUT)
        async with SessionLocal() as session:
            rows = list(
                await session.scalars(select(AppRelease).where(AppRelease.app_id == app_id))
            )
        assert len(rows) == 1
        return list(created), audits, rows[0]
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(AppRelease).where(AppRelease.app_id == app_id))
            await session.execute(delete(App).where(App.id == app_id))
            await session.commit()


async def test_report_then_app_put_concurrent(monkeypatch: pytest.MonkeyPatch) -> None:
    created, audits, row = await _race(monkeypatch, _report_op, _put_op)
    assert created == [True, False]
    assert (row.status, row.created_via) == ("deployed", "deployer")
    assert row.git_sha is not None and len(row.images) == 1
    assert audits == ["release.created", "release.updated"]


async def test_app_put_then_report_concurrent(monkeypatch: pytest.MonkeyPatch) -> None:
    created, audits, row = await _race(monkeypatch, _put_op, _report_op)
    assert created == [True, False]
    assert (row.status, row.created_via) == ("deployed", "app")
    assert row.git_sha is not None and len(row.images) == 1  # the app's body is intact
    assert audits == ["release.created", "release.deployment_reported"]


async def test_parallel_identical_reports_one_create(monkeypatch: pytest.MonkeyPatch) -> None:
    created, audits, row = await _race(monkeypatch, _report_op, _report_op)
    assert sorted(created) == [False, True]
    assert audits == ["release.created"]
    assert row.status == "deployed"
