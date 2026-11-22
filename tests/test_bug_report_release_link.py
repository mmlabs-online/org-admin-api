"""ADM-038 (ADR-0028): the release a report links to at submit, and what a release delete does."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.bug_reports.models import BugReport
from app.modules.rbac.models import App, Role
from tests.conftest import MakeClient
from tests.rbac_factories import make_user
from tests.release_factories import make_release
from tests.test_bug_reports_submit import URL, _app, _body, _headers, _member

HOUR = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(UTC)


async def _submit(
    client: AsyncClient, db: AsyncSession, role: Role, version: str | None, key: str = "ricotdin"
) -> BugReport:
    """Submit as a fresh member; the response is always 201."""
    _, raw, account = await _member(db, role)
    extra: dict[str, Any] = {} if version is None else {"app_version": version}
    resp = await client.post(URL, json=_body(account, **extra), headers=_headers(raw, key))
    assert resp.status_code == 201, resp.text
    row = await db.get(BugReport, uuid.UUID(resp.json()["id"]), populate_existing=True)
    assert row is not None
    return row


async def _deployed(db: AsyncSession, app: App, at: datetime, **kw: Any) -> Any:
    return await make_release(db, app, status="deployed", deployed_at=at, **kw)


async def test_submit_reported_production_version_links_release(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    rel = await make_release(db_session, app, version="v1.2.3")
    row = await _submit(client, db_session, role, "v1.2.3")
    assert (row.release_id, row.release_match) == (rel.id, "reported")
    assert row.app_version == "v1.2.3"


async def test_submit_reported_staging_version_links_release(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    rel = await make_release(
        db_session, app, environment="staging", version="sha-abc1234", git_sha="abc1234" + "0" * 33
    )
    row = await _submit(client, db_session, role, "sha-abc1234")
    assert (row.release_id, row.release_match) == (rel.id, "reported")


async def test_submit_reported_matches_pending_and_failed_release(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    pending = await make_release(db_session, app, version="v1.0.0", status="pending")
    failed = await make_release(db_session, app, version="v1.0.1", status="failed")
    assert (await _submit(client, db_session, role, "v1.0.0")).release_id == pending.id
    assert (await _submit(client, db_session, role, "v1.0.1")).release_id == failed.id


async def test_submit_reported_wins_over_newer_deployed_release(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    old = await _deployed(db_session, app, _now() - 2 * HOUR, version="v1.0.0")
    await _deployed(db_session, app, _now() - HOUR, version="v2.0.0")
    row = await _submit(client, db_session, role, "v1.0.0")
    assert (row.release_id, row.release_match) == (old.id, "reported")


async def test_submit_without_version_infers_latest_deployed_production(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    await _deployed(db_session, app, _now() - 3 * HOUR, version="v1.0.0")
    latest = await _deployed(db_session, app, _now() - HOUR, version="v1.1.0")
    row = await _submit(client, db_session, role, None)
    assert (row.release_id, row.release_match) == (latest.id, "inferred")


async def test_inferred_ignores_future_deployed_at_and_non_deployed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    live = await _deployed(db_session, app, _now() - 2 * HOUR, version="v1.0.0")
    await _deployed(db_session, app, _now() + HOUR, version="v2.0.0")
    await make_release(db_session, app, version="v3.0.0", status="pending")
    await make_release(db_session, app, version="v4.0.0", status="failed")
    row = await _submit(client, db_session, role, None)
    assert (row.release_id, row.release_match) == (live.id, "inferred")


async def test_inferred_ignores_staging_releases(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    await _deployed(
        db_session,
        app,
        _now() - HOUR,
        environment="staging",
        version="sha-abc1234",
        git_sha="abc1234" + "0" * 33,
    )
    row = await _submit(client, db_session, role, None)
    assert (row.release_id, row.release_match) == (None, "none")


async def test_submit_unknown_version_falls_back_to_inferred(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    live = await _deployed(db_session, app, _now() - HOUR, version="v1.0.0")
    for version in ("v9.9.9", "1.2"):
        row = await _submit(client, db_session, role, version)
        assert (row.release_id, row.release_match) == (live.id, "inferred")
        assert row.app_version == version


async def test_submit_version_match_is_exact_and_case_sensitive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    await make_release(db_session, app, version="v1.2.3")
    for version in ("1.2.3", "V1.2.3"):
        row = await _submit(client, db_session, role, version)
        assert (row.release_id, row.release_match) == (None, "none")


async def test_submit_no_release_gives_none(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _app(db_session)
    row = await _submit(client, db_session, role, "v1.2.3")
    assert (row.release_id, row.release_match) == (None, "none")


async def test_submit_other_apps_release_never_links(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _app(db_session)
    other, _ = await _app(db_session, "otherapp")
    await _deployed(db_session, other, _now() - HOUR, version="v1.2.3")
    row = await _submit(client, db_session, role, "v1.2.3")
    assert (row.release_id, row.release_match) == (None, "none")


async def test_submit_audit_has_release_id_and_match_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    rel = await make_release(db_session, app, version="v1.2.3")
    row = await _submit(client, db_session, role, "v1.2.3")
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "bug_report.created", AuditLog.target_id == row.id
        )
    )
    assert audit is not None
    assert audit.changes == {
        "attachment_count": 0,
        "release_id": str(rel.id),
        "release_match": "reported",
    }
    assert "v1.2.3" not in str(audit.changes)


async def test_release_delete_keeps_report_nulls_link_keeps_version(
    client: AsyncClient, db_session: AsyncSession, make_client: MakeClient
) -> None:
    app, role = await _app(db_session)
    rel = await make_release(db_session, app, version="v1.2.3")
    row = await _submit(client, db_session, role, "v1.2.3")
    report_id, rel_id = row.id, rel.id
    staff = await make_user(db_session)
    async with make_client(admin_permissions=["admin:*"], admin_user=staff) as c:
        assert (await c.delete(f"/api/v1/releases/{rel_id}")).status_code == 204
        body = (await c.get(f"/api/v1/bug-reports/{report_id}")).json()
    assert body["release"] is None
    assert (body["release_match"], body["app_version"]) == ("reported", "v1.2.3")
