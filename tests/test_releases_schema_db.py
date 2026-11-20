"""ADM-033: CHECK, unique and FK behaviour of `app_releases` by raw insert."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App
from tests.rbac_factories import make_app
from tests.release_factories import make_release

SHA = "1" * 40
NOW = datetime(2026, 1, 1, tzinfo=UTC)
BASE: dict[str, Any] = {
    "environment": "production",
    "version": "v1.2.3",
    "status": "pending",
    "created_via": "app",
}
_JSON = {"images", "changes"}


async def _insert(db: AsyncSession, app_id: uuid.UUID, **cols: Any) -> None:
    values: dict[str, Any] = {**BASE, **cols}
    params = {k: (json.dumps(v) if k in _JSON else v) for k, v in values.items()}
    names = ["id", "app_id", *values]
    marks = [f"CAST(:{n} AS jsonb)" if n in _JSON else f":{n}" for n in names]
    sql = f"INSERT INTO app_releases ({', '.join(names)}) VALUES ({', '.join(marks)})"  # noqa: S608
    await db.execute(text(sql), {"id": uuid.uuid4(), "app_id": app_id, **params})


@pytest.fixture
async def app(db_session: AsyncSession) -> App:
    return await make_app(db_session, "relapp")


async def _refused(db: AsyncSession, app: App, name: str | None = None, **cols: Any) -> None:
    with pytest.raises(IntegrityError) as exc:
        async with db.begin_nested():
            await _insert(db, app.id, **cols)
    if name is not None:
        assert name in str(exc.value)


@pytest.mark.parametrize("version", ["v01.2.3", "v1.2", "v1.2.3-rc1", "1.2.3", "sha-abcdef0"])
async def test_rejects_bad_production_version(
    db_session: AsyncSession, app: App, version: str
) -> None:
    await _refused(db_session, app, "version_matches_environment", version=version)


@pytest.mark.parametrize("version", ["v1.2.3", "sha-ABCDEF0", "sha-abcdef"])
async def test_rejects_bad_staging_version(
    db_session: AsyncSession, app: App, version: str
) -> None:
    await _refused(
        db_session, app, "version_matches_environment", environment="staging", version=version
    )


async def test_rejects_bad_environment_status_created_via(
    db_session: AsyncSession, app: App
) -> None:
    await _refused(db_session, app, "environment_valid", environment="prod")
    await _refused(db_session, app, "status_valid", status="unknown")
    await _refused(db_session, app, "created_via_valid", created_via="staff")


async def test_rejects_bad_git_sha_and_staging_mismatch(db_session: AsyncSession, app: App) -> None:
    await _refused(db_session, app, "git_sha_format", git_sha="A" * 40)
    await _refused(db_session, app, "git_sha_format", git_sha="a" * 39)
    await _refused(
        db_session,
        app,
        "staging_version_matches_sha",
        environment="staging",
        version="sha-1111111",
        git_sha="2222222" + "0" * 33,
    )


async def test_rejects_deployed_at_mismatch(db_session: AsyncSession, app: App) -> None:
    await _refused(db_session, app, "deployed_iff_deployed_at", status="deployed")
    await _refused(db_session, app, "deployed_iff_deployed_at", deployed_at=NOW)
    await _refused(
        db_session,
        app,
        "deployed_iff_deployed_at",
        status="failed",
        deployed_at=NOW,
    )


async def test_rejects_oversized_or_non_array_json(db_session: AsyncSession, app: App) -> None:
    await _refused(db_session, app, "images_bounded", images=[{}] * 11)
    await _refused(db_session, app, "changes_bounded", changes=[{}] * 201)
    await _refused(db_session, app, "changes_bounded", changes={})  # IntegrityError, not DataError


async def test_rejects_bad_urls(db_session: AsyncSession, app: App) -> None:
    await _refused(
        db_session,
        app,
        "deployment_pr_format",
        deployment_pr="https://github.com/other/repo/pull/1",
    )
    await _refused(
        db_session,
        app,
        "deploy_run_url_format",
        deploy_run_url="https://github.com/mmlabs-online/deployment/actions/runs/1/jobs/1",
    )


async def test_unique_app_environment_version(db_session: AsyncSession, app: App) -> None:
    await _insert(db_session, app.id)
    await _refused(db_session, app, "uq_app_releases_app_id_environment_version")
    other = await make_app(db_session, "relother")
    await _insert(db_session, other.id)
    await _insert(db_session, app.id, environment="staging", version="sha-1111111", git_sha=SHA)


async def test_accepts_report_created_and_boundaries(db_session: AsyncSession, app: App) -> None:
    await _insert(db_session, app.id, created_via="deployer")  # thin record, all optionals NULL
    await _insert(db_session, app.id, environment="staging", version="sha-1111111", git_sha=SHA)
    await _insert(db_session, app.id, version="v0.0.0", images=[{}] * 10, changes=[{}] * 200)
    await _insert(
        db_session,
        app.id,
        version="v0.0.1",
        deployment_pr="https://github.com/mmlabs-online/deployment/pull/7",
        deploy_run_url="https://github.com/mmlabs-online/deployment/actions/runs/1/attempts/2",
    )


async def test_app_with_release_cannot_be_deleted_at_db(db_session: AsyncSession, app: App) -> None:
    await make_release(db_session, app)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(text("DELETE FROM apps WHERE id = :id"), {"id": app.id})
