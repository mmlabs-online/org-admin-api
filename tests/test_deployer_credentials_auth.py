"""ADM-035: `current_deployer` on a test-only probe route (no production route uses it yet)."""

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.db.session import get_session
from app.main import create_app
from app.modules.deployer_credentials.deps import DeployerDep
from app.modules.deployer_credentials.repository import DeployerCredentialRepository
from app.modules.deployer_credentials.service import DeployerCredentialService
from tests.admin_auth_factories import bearer, make_staff, mint
from tests.app_credential_factories import make_credential
from tests.deployer_credential_factories import make_deployer_credential
from tests.rbac_factories import make_app

PROBE = "/__probe"
CHALLENGE = 'Bearer realm="deployer"'
BODY = {"error": {"code": "unauthorized", "message": "Authentication required", "details": None}}


@pytest.fixture
async def probe(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    router = APIRouter()

    @router.get(PROBE)
    async def probe_route(deployer: DeployerDep) -> dict[str, Any]:
        return {
            "credential_id": str(deployer.credential_id),
            "key_id": deployer.key_id,
            "actor": deployer.actor.name,
            "privileged": deployer.actor.privileged,
        }

    app.include_router(router)

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_valid_key_authenticates(probe: AsyncClient, db_session: AsyncSession) -> None:
    row, raw = await make_deployer_credential(db_session)
    resp = await probe.get(PROBE, headers=bearer(raw))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "credential_id": str(row.id),
        "key_id": row.key_id,
        "actor": f"deployer:{row.key_id}",
        "privileged": False,
    }


async def test_valid_key_touches_last_used(probe: AsyncClient, db_session: AsyncSession) -> None:
    row, raw = await make_deployer_credential(db_session)
    assert row.last_used_at is None
    await probe.get(PROBE, headers=bearer(raw))
    await db_session.refresh(row)
    assert row.last_used_at is not None


async def _headers(case: str, db: AsyncSession) -> dict[str, str]:
    row, _ = await make_deployer_credential(db)
    if case == "none":
        return {}
    if case == "basic":
        return {"Authorization": "Basic Zm9vOmJhcg=="}
    if case == "garbage":
        return bearer("garbage")
    if case == "app_key":
        _, app_raw = await make_credential(db, await make_app(db, "stoktok"))
        return bearer(app_raw)
    if case == "admin_jwt":
        return bearer(mint(await make_staff(db, "admin:*")))
    if case == "unknown":
        return bearer(f"mmdk_{'0' * 16}_{'A' * 43}")
    if case == "wrong_secret":
        return bearer(f"mmdk_{row.key_id}_{'A' * 43}")
    _, revoked_raw = await make_deployer_credential(db, revoked=True)
    return bearer(revoked_raw)


@pytest.mark.parametrize(
    "case",
    ["none", "basic", "garbage", "app_key", "admin_jwt", "unknown", "wrong_secret", "revoked"],
)
async def test_probe_rejections_identical(
    probe: AsyncClient, db_session: AsyncSession, case: str
) -> None:
    resp = await probe.get(PROBE, headers=await _headers(case, db_session))
    assert resp.status_code == 401
    assert resp.json() == BODY
    assert resp.headers["www-authenticate"] == CHALLENGE


async def test_revoked_between_lookup_and_touch_401(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    row, raw = await make_deployer_credential(db_session)
    repo = DeployerCredentialRepository(db_session)
    real = DeployerCredentialRepository.get_by_key_id

    async def lookup_then_revoke(self: DeployerCredentialRepository, key_id: str) -> Any:
        found = await real(self, key_id)
        await repo.revoke(row.id)
        return found

    monkeypatch.setattr(DeployerCredentialRepository, "get_by_key_id", lookup_then_revoke)
    with pytest.raises(UnauthorizedError):
        await DeployerCredentialService(db_session).authenticate(raw)


async def test_wrong_prefix_no_db_read(
    probe: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_: Any) -> None:
        raise AssertionError("database read")

    _, app_raw = await make_credential(db_session, await make_app(db_session, "stoktok"))
    monkeypatch.setattr(DeployerCredentialRepository, "get_by_key_id", boom)
    for raw in (app_raw, "a.b.c", "garbage"):
        resp = await probe.get(PROBE, headers=bearer(raw))
        assert resp.status_code == 401


async def test_rejection_logs_reason_and_key_id_only(
    probe: AsyncClient, db_session: AsyncSession
) -> None:
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("app.modules.deployer_credentials.service")
    handler, level, disabled = Capture(), logger.level, logger.disabled
    logger.disabled = False  # Alembic's `fileConfig` (test session setup) disables app loggers
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        row, raw = await make_deployer_credential(db_session)
        bad = f"mmdk_{row.key_id}_{'A' * 43}"
        await probe.get(PROBE, headers=bearer(bad))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.disabled = disabled
    assert records == [f"deployer_credential_denied reason=secret key_id={row.key_id}"]
    assert bad not in " ".join(records) and raw not in " ".join(records)
