"""ADM-028: parallel submits at the open-report cap on real Postgres (committed data, cleanup).

Cap = 2 with one open report already: three parallel submits of one user must give exactly one
201 and two 429, because the per-user advisory lock serializes check-then-insert. The audit
write is stubbed so no append-only `audit_log` rows are left behind.
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select

from app.core.config import Settings
from app.db.session import SessionLocal
from app.main import create_app
from app.modules.audit.service import AuditLogger
from app.modules.bug_reports.models import BugReport
from app.modules.bug_reports.repository import BugReportRepository
from app.modules.rbac.models import Role, RoleAssignment
from tests.auth_factories import auth_headers, make_device, make_login_app, make_login_user
from tests.test_login_concurrency import TIMEOUT, _cleanup

URL = "/api/v1/auth/bug-reports"


@dataclass
class ReportRace:
    app: Any
    app_key: str
    cookie: str
    account_id: uuid.UUID
    user_id: uuid.UUID

    async def submit(self) -> Response:
        transport = ASGITransport(app=self.app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                URL,
                json={"account_id": str(self.account_id), "description": "race"},
                headers=auth_headers(self.app_key, cookie=self.cookie),
            )


def _hold_after_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each submit pauses after its open count (up to 0.5 s) so all three would pass the cap
    check together; the advisory lock makes the others wait before they ever count."""
    barrier = asyncio.Barrier(3)
    real = BugReportRepository.count_open

    async def count_open(self: BugReportRepository, user_id: uuid.UUID, app_id: uuid.UUID) -> int:
        count = await real(self, user_id, app_id)
        with contextlib.suppress(TimeoutError, asyncio.BrokenBarrierError):
            await asyncio.wait_for(barrier.wait(), timeout=0.5)
        return count

    monkeypatch.setattr(BugReportRepository, "count_open", count_open)


@pytest.fixture
async def report_race(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ReportRace]:
    app_key = "brace-" + uuid.uuid4().hex[:8]
    async with SessionLocal() as session:
        rbac_app = await make_login_app(session, key=app_key)
        role = Role(app_id=rbac_app.id, key=f"{app_key}:member", name="Member")
        session.add(role)
        await session.flush()
        user = await make_login_user(session)
        session.add(RoleAssignment(user_id=user.id, role_id=role.id))
        cookie, _, (account,) = await make_device(session, user)
        session.add(BugReport(app_id=rbac_app.id, user_id=user.id, description="existing"))
        await session.commit()
        app_id = rbac_app.id

    async def no_audit(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(AuditLogger, "record", no_audit)
    _hold_after_count(monkeypatch)
    app = create_app(Settings(bug_report_max_open_per_user_app=2, bug_report_user_rate_limit=1000))
    try:
        yield ReportRace(app, app_key, cookie, account.id, user.id)
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(BugReport).where(BugReport.user_id == user.id))
            await session.execute(delete(RoleAssignment).where(RoleAssignment.user_id == user.id))
            await session.execute(delete(Role).where(Role.app_id == app_id))
            await _cleanup(session, user.id, app_key, b"")


async def test_parallel_submits_at_cap_one_wins(report_race: ReportRace) -> None:
    responses = await asyncio.wait_for(
        asyncio.gather(*(report_race.submit() for _ in range(3))), timeout=TIMEOUT
    )
    assert sorted(r.status_code for r in responses) == [201, 429, 429]
    async with SessionLocal() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(BugReport)
            .where(BugReport.user_id == report_race.user_id)
        )
    assert total == 2
