"""ADM-035: parallel issue at the limit on real Postgres (committed data, cleanup).

One active key already: two parallel POSTs must give one 201 and one 409, because the advisory
lock serialises check-then-insert. The audit write is stubbed so no append-only rows are left.
"""

import asyncio
import contextlib
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select

from app.core.config import Settings
from app.db.session import SessionLocal
from app.main import create_app
from app.modules.admin_auth.deps import current_admin
from app.modules.admin_auth.service import AdminPrincipal
from app.modules.audit.service import AuditLogger
from app.modules.deployer_credentials.models import DeployerCredential
from app.modules.deployer_credentials.repository import DeployerCredentialRepository
from tests.auth_factories import make_login_user
from tests.deployer_credential_factories import make_deployer_credential
from tests.test_login_concurrency import TIMEOUT, _cleanup

URL = "/api/v1/deployer-credentials"


async def test_parallel_issue_at_limit_one_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = asyncio.Barrier(2)
    real = DeployerCredentialRepository.count_active

    async def count_active(self: DeployerCredentialRepository) -> int:
        count = await real(self)
        with contextlib.suppress(TimeoutError, asyncio.BrokenBarrierError):
            await asyncio.wait_for(barrier.wait(), timeout=0.5)
        return count

    async def no_audit(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(DeployerCredentialRepository, "count_active", count_active)
    monkeypatch.setattr(AuditLogger, "record", no_audit)

    async with SessionLocal() as session:
        staff = await make_login_user(session)
        await make_deployer_credential(session, created_by=staff.id)
        await session.commit()
        staff_id = staff.id
    principal = AdminPrincipal(staff_id, frozenset({"admin:*"}), "test")

    async def issue() -> Response:
        app = create_app(Settings())
        app.dependency_overrides[current_admin] = lambda: principal
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(URL)

    try:
        responses = await asyncio.wait_for(asyncio.gather(issue(), issue()), timeout=TIMEOUT)
        assert sorted(r.status_code for r in responses) == [201, 409]
        async with SessionLocal() as session:
            active = await session.scalar(
                select(func.count())
                .select_from(DeployerCredential)
                .where(DeployerCredential.revoked_at.is_(None))
            )
        assert active == 2
    finally:
        async with SessionLocal() as session:
            await session.execute(
                delete(DeployerCredential).where(DeployerCredential.created_by == staff_id)
            )
            await _cleanup(session, staff_id, "unused-" + uuid.uuid4().hex[:6], b"")
