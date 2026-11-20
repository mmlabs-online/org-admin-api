"""PUT /api/v1/apps/{key}/releases/{environment}/{version}/deployment (ADM-036, ADR-0021)."""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.deployer_credentials.models import DeployerCredential
from app.modules.releases.models import AppRelease
from app.modules.releases.repository import AppReleaseRepository
from tests.admin_auth_factories import bearer, make_staff, mint
from tests.app_credential_factories import make_credential
from tests.conftest import MakeClient
from tests.deployer_credential_factories import make_deployer_credential
from tests.rbac_factories import make_app
from tests.release_factories import make_release

V1 = "/api/v1"
VERSION = "v1.2.3"
RUN = "https://github.com/mmlabs-online/deployment/actions/runs"
URL1, URL2 = f"{RUN}/101", f"{RUN}/102"
ACTIONS = ("release.created", "release.updated", "release.deployment_reported")
SHA = "0123456789abcdef0123456789abcdef01234567"


@dataclass
class Env:
    app_id: uuid.UUID
    key: str
    key_id: str
    raw: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.raw}"}

    def url(self, version: str = VERSION, environment: str = "production") -> str:
        return f"{V1}/apps/{self.key}/releases/{environment}/{version}/deployment"


def _now() -> datetime:
    return datetime.now(UTC)


def deployed(url: str = URL1, at: datetime | None = None) -> dict[str, Any]:
    return {
        "status": "deployed",
        "deploy_run_url": url,
        "deployed_at": (at or _now()).isoformat(),
    }


def failed(url: str = URL1) -> dict[str, Any]:
    return {"status": "failed", "deploy_run_url": url}


async def _env(db: AsyncSession, **app_kwargs: Any) -> Env:
    key = f"app-{uuid.uuid4().hex[:8]}"
    app = await make_app(db, key, **app_kwargs)
    row, raw = await make_deployer_credential(db)
    return Env(app.id, key, row.key_id, raw)


async def _report(
    client: AsyncClient, env: Env, data: Any, *, version: str = VERSION, **kw: Any
) -> Response:
    return await client.put(
        env.url(version, kw.pop("environment", "production")), json=data, headers=env.headers, **kw
    )


async def _audit(db: AsyncSession, key: str) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.app_key == key, AuditLog.action.in_(ACTIONS))
        .order_by(AuditLog.action, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _release(db: AsyncSession, env: Env, version: str = VERSION) -> AppRelease:
    row = await db.scalar(
        select(AppRelease)
        .where(AppRelease.app_id == env.app_id, AppRelease.version == version)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def _seed(
    db: AsyncSession, env: Env, status: str, url: str | None = None, at: datetime | None = None
) -> AppRelease:
    class _App:
        id = env.app_id

    return await make_release(
        db,
        _App(),  # type: ignore[arg-type]
        version=VERSION,
        status=status,
        deploy_run_url=url,
        deployed_at=at if status == "deployed" else None,
        created_via="deployer",
        git_sha=None,
    )


# --- create ----------------------------------------------------------------------


@pytest.mark.parametrize("data", [deployed(), failed()], ids=["deployed", "failed"])
async def test_create_201(client: AsyncClient, db_session: AsyncSession, data: Any) -> None:
    env = await _env(db_session)
    resp = await _report(client, env, data)
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert (out["status"], out["created_via"], out["deploy_run_url"]) == (
        data["status"],
        "deployer",
        URL1,
    )
    assert out["git_sha"] is None and out["deployment_pr"] is None
    assert out["has_migrations"] is None and out["changes_count"] == 0
    row = await _release(db_session, env)
    assert row.images == [] and row.changes == []  # same shape as the app write's "empty"
    (audit,) = await _audit(db_session, env.key)
    assert (audit.action, audit.actor_kind) == ("release.created", "system")
    assert audit.actor_label == f"deployer:{env.key_id}"
    assert audit.changes["created_via"] == {"from": None, "to": "deployer"}
    cred = await db_session.scalar(
        select(DeployerCredential)
        .where(DeployerCredential.key_id == env.key_id)
        .execution_options(populate_existing=True)
    )
    assert cred is not None and cred.last_used_at is not None


async def test_retry_of_create_200_same_body_one_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    data = deployed()
    first = await _report(client, env, data)
    second = await _report(client, env, data)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json() == first.json()
    assert len(await _audit(db_session, env.key)) == 1


# --- transitions -----------------------------------------------------------------


async def test_pending_to_deployed_200(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    class_app = await _seed(db_session, env, "pending")
    class_app.git_sha = SHA
    await db_session.flush()
    resp = await _report(client, env, deployed())
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "deployed" and resp.json()["git_sha"] == SHA
    row = await _release(db_session, env)
    assert (row.deploy_run_url, row.created_via) == (URL1, "deployer")
    (audit,) = await _audit(db_session, env.key)
    assert audit.action == "release.deployment_reported"
    assert audit.changes["status"] == {"from": "pending", "to": "deployed"}


async def test_pending_to_failed_200(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    await _seed(db_session, env, "pending")
    resp = await _report(client, env, failed())
    assert resp.status_code == 200 and resp.json()["status"] == "failed"
    assert resp.json()["deployed_at"] is None
    assert [a.action for a in await _audit(db_session, env.key)] == ["release.deployment_reported"]


async def test_failed_to_deployed_200(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    await _seed(db_session, env, "failed", URL1)
    resp = await _report(client, env, deployed(URL2))
    assert resp.status_code == 200 and resp.json()["status"] == "deployed"
    assert resp.json()["deploy_run_url"] == URL2
    assert [a.action for a in await _audit(db_session, env.key)] == ["release.deployment_reported"]


async def test_failed_to_failed_new_url_200_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    await _seed(db_session, env, "failed", URL1)
    resp = await _report(client, env, failed(URL2))
    assert resp.status_code == 200 and resp.json()["deploy_run_url"] == URL2
    (audit,) = await _audit(db_session, env.key)
    assert audit.changes["fields"]["to"] == ["deploy_run_url"] and "status" not in audit.changes


async def test_failed_to_failed_same_url_noop(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    await _seed(db_session, env, "failed", URL1)
    updated = (await _release(db_session, env)).updated_at
    assert (await _report(client, env, failed(URL1))).status_code == 200
    assert (await _release(db_session, env)).updated_at == updated
    assert await _audit(db_session, env.key) == []


async def test_deployed_same_url_noop(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    at = _now() - timedelta(hours=1)
    await _seed(db_session, env, "deployed", URL1, at)
    resp = await _report(client, env, deployed(URL1, _now()))
    assert resp.status_code == 200
    row = await _release(db_session, env)
    assert row.deployed_at == at
    assert await _audit(db_session, env.key) == []


async def test_deployed_new_url_redeploy_200_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    await _seed(db_session, env, "deployed", URL1, _now() - timedelta(hours=1))
    new_at = _now()
    resp = await _report(client, env, deployed(URL2, new_at))
    assert resp.status_code == 200 and resp.json()["deploy_run_url"] == URL2
    row = await _release(db_session, env)
    assert (row.deploy_run_url, row.deployed_at) == (URL2, new_at)
    (audit,) = await _audit(db_session, env.key)
    assert audit.action == "release.deployment_reported"
    assert audit.changes["fields"]["to"] == ["deploy_run_url", "deployed_at"]


async def test_deployed_new_url_older_deployed_at_noop(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    at = _now() - timedelta(hours=1)
    await _seed(db_session, env, "deployed", URL1, at)
    resp = await _report(client, env, deployed(URL2, at - timedelta(hours=2)))
    assert resp.status_code == 200
    row = await _release(db_session, env)
    assert (row.deploy_run_url, row.deployed_at) == (URL1, at)
    assert await _audit(db_session, env.key) == []


async def test_deployed_to_failed_409_unchanged_no_audit(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging.getLogger("app.modules.releases.deployment"), "disabled", False)
    env = await _env(db_session)
    await _seed(db_session, env, "deployed", URL1, _now())
    updated = (await _release(db_session, env)).updated_at
    with caplog.at_level(logging.DEBUG):
        resp = await _report(client, env, failed(URL2))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "release_already_deployed"
    row = await _release(db_session, env)
    assert (row.status, row.deploy_run_url, row.updated_at) == ("deployed", URL1, updated)
    assert await _audit(db_session, env.key) == []
    assert "release_already_deployed" in caplog.text and env.key_id in caplog.text
    assert URL2 not in caplog.text and env.raw not in caplog.text


# --- interplay with the app write (ADM-034) ----------------------------------------


def _put_body(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "git_sha": SHA,
        "deployment_pr": "https://github.com/mmlabs-online/deployment/pull/7",
        "has_migrations": False,
        "images": [{"name": "mmlabs/api", "digest": "sha256:" + "a" * 64}],
        "changes": [
            {"pr_url": "https://github.com/mmlabs-online/org-admin-api/pull/1", "title": "One"}
        ],
    }
    return {**base, **over}


async def _app_env(db: AsyncSession, env: Env) -> dict[str, str]:
    class _App:
        id = env.app_id

    _, raw = await make_credential(db, _App(), scope="releases")  # type: ignore[arg-type]
    return {"Authorization": f"Bearer {raw}"}


async def test_report_first_then_app_put_enriches(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    at = _now()
    assert (await _report(client, env, deployed(URL1, at))).status_code == 201
    headers = await _app_env(db_session, env)
    resp = await client.put(
        f"{V1}/apps/{env.key}/releases/production/{VERSION}", json=_put_body(), headers=headers
    )
    assert resp.status_code == 200, resp.text  # enrichment, never a spurious 409
    out = resp.json()
    assert out["git_sha"] == SHA and out["has_migrations"] is False
    assert len(out["images"]) == 1 and len(out["changes"]) == 1
    row = await _release(db_session, env)
    assert (row.status, row.deployed_at, row.deploy_run_url, row.created_via) == (
        "deployed",
        at,
        URL1,
        "deployer",
    )
    assert [a.action for a in await _audit(db_session, env.key)] == [
        "release.created",
        "release.updated",
    ]


async def test_report_on_pending_then_identical_app_put_noop(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    headers = await _app_env(db_session, env)
    url = f"{V1}/apps/{env.key}/releases/production/{VERSION}"
    assert (await client.put(url, json=_put_body(), headers=headers)).status_code == 201
    assert (await _report(client, env, deployed())).status_code == 200
    again = await client.put(url, json=_put_body(), headers=headers)
    assert again.status_code == 200 and again.json()["status"] == "deployed"
    assert [a.action for a in await _audit(db_session, env.key)] == [
        "release.created",
        "release.deployment_reported",
    ]


# --- live ------------------------------------------------------------------------


async def _live(client: AsyncClient, db: AsyncSession, env: Env) -> list[str]:
    staff = await make_staff(db, "admin:*")
    resp = await client.get(
        f"{V1}/releases/live", params={"app": env.key}, headers=bearer(mint(staff))
    )
    assert resp.status_code == 200, resp.text
    return [i["version"] for i in resp.json()["items"]]


async def test_rollback_report_moves_live(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    t = _now() - timedelta(hours=3)
    assert (await _report(client, env, deployed(URL1, t), version="v1.0.0")).status_code == 201
    later = deployed(URL2, t + timedelta(hours=1))
    assert (await _report(client, env, later, version="v2.0.0")).status_code == 201
    assert await _live(client, db_session, env) == ["v2.0.0"]
    back = deployed(f"{RUN}/103", t + timedelta(hours=2))
    assert (await _report(client, env, back, version="v1.0.0")).status_code == 200
    assert await _live(client, db_session, env) == ["v1.0.0"]


async def test_stale_report_does_not_move_live(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    t = _now() - timedelta(hours=3)
    await _report(client, env, deployed(URL1, t), version="v1.0.0")
    await _report(client, env, deployed(URL2, t + timedelta(hours=1)), version="v2.0.0")
    stale = deployed(f"{RUN}/103", t - timedelta(hours=1))
    assert (await _report(client, env, stale, version="v1.0.0")).status_code == 200
    assert await _live(client, db_session, env) == ["v2.0.0"]


# --- apps --------------------------------------------------------------------------


async def test_disabled_app_accepted(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session, status="disabled")
    assert (await _report(client, env, deployed())).status_code == 201


async def test_admin_app_accepted(client: AsyncClient, db_session: AsyncSession) -> None:
    _, raw = await make_deployer_credential(db_session)
    resp = await client.put(
        f"{V1}/apps/admin/releases/production/{VERSION}/deployment",
        json=deployed(),
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["app_key"] == "admin"


async def test_unknown_app_404_after_auth(client: AsyncClient, db_session: AsyncSession) -> None:
    _, raw = await make_deployer_credential(db_session)
    url = f"{V1}/apps/nope-{uuid.uuid4().hex[:6]}/releases/production/{VERSION}/deployment"
    ok = await client.put(url, json=deployed(), headers={"Authorization": f"Bearer {raw}"})
    assert ok.status_code == 404 and ok.json()["error"]["code"] == "not_found"
    assert (await client.put(url, json=deployed())).status_code == 401


# --- auth ----------------------------------------------------------------------------


async def test_401_uniform(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    revoked, revoked_raw = await make_deployer_credential(db_session, revoked=True)
    assert revoked.revoked_at is not None
    app = await make_app(db_session, f"ak-{uuid.uuid4().hex[:6]}")
    _, manifest_raw = await make_credential(db_session, app, scope="manifest")
    _, releases_raw = await make_credential(db_session, app, scope="releases")
    staff = await make_staff(db_session, "admin:*")
    prefix, _, secret = env.raw.rpartition("_")
    cases: dict[str, dict[str, str]] = {
        "missing": {},
        "malformed": {"Authorization": "Bearer nonsense"},
        "unknown_key_id": {"Authorization": f"Bearer mmdk_{'0' * 16}_{secret}"},
        "wrong_secret": {"Authorization": f"Bearer {prefix}_{'x' * len(secret)}"},
        "revoked": {"Authorization": f"Bearer {revoked_raw}"},
        "app_manifest": {"Authorization": f"Bearer {manifest_raw}"},
        "app_releases": {"Authorization": f"Bearer {releases_raw}"},
        "admin_jwt": bearer(mint(staff)),
    }
    await db_session.commit()
    bodies = set()
    for name, headers in cases.items():
        resp = await client.put(env.url(), json=deployed(), headers=headers)
        assert resp.status_code == 401, name
        assert resp.headers["www-authenticate"] == 'Bearer realm="deployer"', name
        bodies.add(json.dumps(resp.json(), sort_keys=True))
    assert len(bodies) == 1
    assert (await client.put(env.url(), json=deployed())).json()["error"]["details"] in (None, [])
    assert (
        await db_session.scalar(select(AppRelease.id).where(AppRelease.app_id == env.app_id))
    ) is None


async def test_401_before_body_read(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    big = await client.put(env.url(), content=b"x" * 10240, headers={"Content-Type": "text/plain"})
    assert big.status_code == 401
    bad = await client.put(
        env.url(), content=b"{nope", headers={"Content-Type": "application/json"}
    )
    assert bad.status_code == 401


async def test_rate_limit_429_before_auth(make_client: MakeClient) -> None:
    async with make_client(release_deployment_rate_limit=1) as client:
        url = f"{V1}/apps/stoktok/releases/production/{VERSION}/deployment"
        first = await client.put(url, json=deployed())
        second = await client.put(url, json=deployed())
        other = await client.put(
            f"{V1}/apps/stoktok/releases/production/{VERSION}", json={}
        )  # app_release bucket is separate
    assert first.status_code == 401
    assert second.status_code == 429 and "retry-after" in second.headers
    assert other.status_code == 401


# --- request shape --------------------------------------------------------------------


async def test_body_cap_413(make_client: MakeClient, db_session: AsyncSession) -> None:
    async with make_client(release_deployment_max_bytes=512) as client:
        env = await _env(db_session)
        resp = await client.put(
            env.url(),
            content=json.dumps({**deployed(), "pad": "x" * 600}).encode(),
            headers={**env.headers, "Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "payload_too_large"
        assert (await _report(client, env, deployed())).status_code == 201


async def test_missing_length_411(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)

    async def gen() -> Any:
        yield json.dumps(deployed()).encode()

    resp = await client.put(
        env.url(), content=gen(), headers={**env.headers, "Content-Type": "application/json"}
    )
    assert resp.status_code == 411


async def test_wrong_content_type_415(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await client.put(
        env.url(),
        content=json.dumps(deployed()).encode(),
        headers={**env.headers, "Content-Type": "text/plain"},
    )
    assert resp.status_code == 415


async def test_malformed_json_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await client.put(
        env.url(), content=b"{nope", headers={**env.headers, "Content-Type": "application/json"}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "data",
    [
        {**deployed(), "status": "pending"},
        {**failed(), "deployed_at": _now().isoformat()},
        {"status": "deployed", "deploy_run_url": URL1},
        {**deployed(), "deploy_run_url": "https://evil.example/SECRET-VALUE"},
        {**deployed(), "deploy_run_url": URL1 + "\n"},
        {**deployed(), "created_via": "app"},
        {**deployed(), "deployed_at": "2026-01-01T00:00:00"},
        {"status": "failed"},
        {},
    ],
)
async def test_invalid_body_422(client: AsyncClient, db_session: AsyncSession, data: Any) -> None:
    env = await _env(db_session)
    resp = await client.put(
        env.url(),
        content=json.dumps(data).encode(),
        headers={**env.headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "validation_error"
    assert "SECRET-VALUE" not in resp.text
    assert await _audit(db_session, env.key) == []


@pytest.mark.parametrize(
    ("environment", "version"),
    [
        ("prod", VERSION),
        ("production", "v01.2.3"),
        ("production", "v1.2.3%0A"),
        ("production", "sha-abcdef0"),
        ("staging", VERSION),
        ("staging", "sha-ABCDEF0"),
        ("staging", "sha-abcdef0%0A"),
        ("production", "v" + "1" * 33),
    ],
)
async def test_bad_path_422(
    client: AsyncClient, db_session: AsyncSession, environment: str, version: str
) -> None:
    env = await _env(db_session)
    resp = await client.put(
        f"{V1}/apps/{env.key}/releases/{environment}/{version}/deployment",
        json=deployed(),
        headers=env.headers,
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("delta", "reason"),
    [(timedelta(minutes=10), "too_far_in_future"), (-timedelta(days=31), "too_old")],
)
async def test_deployed_at_window_422(
    client: AsyncClient, db_session: AsyncSession, delta: timedelta, reason: str
) -> None:
    env = await _env(db_session)
    resp = await _report(client, env, deployed(URL1, _now() + delta))
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == [{"field": "deployed_at", "reason": reason}]
    assert await _audit(db_session, env.key) == []


# --- audit and races -----------------------------------------------------------------


async def test_audit_changes_ids_and_status_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    assert (await _report(client, env, failed(URL1))).status_code == 201
    assert (await _report(client, env, deployed(URL2))).status_code == 200
    rows = await _audit(db_session, env.key)
    assert len(rows) == 2
    allowed = {"environment", "version", "status", "created_via", "fields"}
    for row in rows:
        assert set(row.changes) <= allowed
        dumped = json.dumps(row.changes)
        assert "https://" not in dumped and "github.com" not in dumped


async def test_staff_delete_race_retries_then_creates(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = await _env(db_session)
    await _seed(db_session, env, "failed", URL1)
    real_insert = AppReleaseRepository.insert_if_absent
    real_get = AppReleaseRepository.get_for_update
    calls = {"insert": 0}

    async def insert(self: AppReleaseRepository, **kw: Any) -> AppRelease | None:
        calls["insert"] += 1
        if calls["insert"] == 2:  # the row is gone by the retry
            await db_session.delete(await real_get(self, env.app_id, "production", VERSION))
            await db_session.flush()
        return await real_insert(self, **kw)

    async def get_none(self: AppReleaseRepository, *a: Any) -> AppRelease | None:
        return None

    monkeypatch.setattr(AppReleaseRepository, "insert_if_absent", insert)
    monkeypatch.setattr(AppReleaseRepository, "get_for_update", get_none)
    resp = await _report(client, env, deployed())
    assert resp.status_code == 201, resp.text


async def test_staff_delete_race_twice_409(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = await _env(db_session)
    await _seed(db_session, env, "failed", URL1)

    async def get_none(self: AppReleaseRepository, *a: Any) -> AppRelease | None:
        return None

    monkeypatch.setattr(AppReleaseRepository, "get_for_update", get_none)
    resp = await _report(client, env, deployed())
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == [{"field": "version", "reason": "retry"}]


async def test_no_cors(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session, origins=["https://stoktok.mmlabs.online"])
    for origin in ("https://admin-portal.mmlabs.online", "https://stoktok.mmlabs.online"):
        resp = await client.put(
            env.url(), json=deployed(), headers={**env.headers, "Origin": origin}
        )
        assert "access-control-allow-origin" not in resp.headers
        pre = await client.options(
            env.url(),
            headers={"Origin": origin, "Access-Control-Request-Method": "PUT"},
        )
        assert "access-control-allow-origin" not in pre.headers
