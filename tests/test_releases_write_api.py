"""PUT /api/v1/apps/{key}/releases/{environment}/{version} (ADM-034, ADR-0021 §3, §4, §7, §8)."""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.modules.app_credentials.models import AppCredential
from app.modules.app_credentials.service import AppCredentialService, AuthenticatedApp
from app.modules.audit.context import AuditContext
from app.modules.audit.models import AuditLog
from app.modules.rbac.schemas import AdminOriginsSet
from app.modules.rbac.service import AppService
from app.modules.releases.app_write import ReleaseAppWriteService
from app.modules.releases.models import AppRelease, ReleaseEnvironment
from app.modules.releases.write_schemas import ReleasePut
from tests.admin_auth_factories import bearer, make_staff, mint
from tests.app_credential_factories import make_credential
from tests.conftest import MakeClient
from tests.rbac_factories import make_app
from tests.release_factories import make_release

V1 = "/api/v1"
VERSION = "v1.2.3"
SHA = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "a" * 64
PR1 = "https://github.com/mmlabs-online/org-admin-api/pull/1"
PR2 = "https://github.com/mmlabs-online/org-admin-api/pull/2"
DEPLOY_PR = "https://github.com/mmlabs-online/deployment/pull/7"
ACTIONS = ("release.created", "release.updated")


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
        return f"{V1}/apps/{self.key}/releases/{environment}/{version}"


def body(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "git_sha": SHA,
        "images": [{"name": "mmlabs/api", "digest": DIGEST}],
        "changes": [{"pr_url": PR1, "title": "First change", "ticket_id": "ADM-1"}],
    }
    return {**base, **over}


async def _env(db: AsyncSession, *, scope: str = "releases", **app_kwargs: Any) -> Env:
    key = f"app-{uuid.uuid4().hex[:8]}"
    app = await make_app(db, key, **app_kwargs)
    row, raw = await make_credential(db, app, scope=scope)
    return Env(app.id, key, row.key_id, raw)


async def _put(
    client: AsyncClient, env: Env, data: Any = None, *, version: str = VERSION, **kw: Any
) -> Response:
    return await client.put(
        env.url(version, kw.pop("environment", "production")),
        json=body() if data is None else data,
        headers=env.headers,
        **kw,
    )


async def _audit(db: AsyncSession, key: str) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.app_key == key, AuditLog.action.in_(ACTIONS))
        .order_by(AuditLog.action, AuditLog.id)  # same transaction: equal `occurred_at`
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


async def _mark(db: AsyncSession, env: Env, status: str, **fields: Any) -> None:
    row = await _release(db, env)
    row.status = status
    for name, value in fields.items():
        setattr(row, name, value)
    if status == "deployed":
        row.deployed_at = datetime.now(UTC)
    await db.flush()


# --- outcomes ------------------------------------------------------------------


async def test_create_201_pending(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await _put(client, env)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert (data["status"], data["created_via"], data["version"]) == ("pending", "app", VERSION)
    assert (data["app_key"], data["environment"], data["git_sha"]) == (env.key, "production", SHA)
    assert data["changes_count"] == 1 and data["images"][0]["digest"] == DIGEST
    assert data["changes"][0]["risk"] is None and data["deployed_at"] is None
    cred = await db_session.scalar(
        select(AppCredential)
        .where(AppCredential.key_id == env.key_id)
        .execution_options(populate_existing=True)
    )
    assert cred is not None and cred.last_used_at is not None
    (audit,) = await _audit(db_session, env.key)
    assert (audit.action, audit.outcome, audit.actor_kind) == (
        "release.created",
        "success",
        "system",
    )
    assert audit.actor_label == f"app:{env.key}/{env.key_id}"
    assert str(audit.target_id) == data["id"]


async def test_pending_replace_200(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    assert (await _put(client, env)).status_code == 201
    new = body(
        has_migrations=True,
        changes=[{"pr_url": PR2, "title": "Second"}],
        images=[{"name": "mmlabs/other", "digest": "sha256:" + "b" * 64}],
    )
    resp = await _put(client, env, new)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_migrations"] is True and data["changes"][0]["pr_url"] == PR2
    assert data["images"][0]["name"] == "mmlabs/other" and data["status"] == "pending"
    rows = await _audit(db_session, env.key)
    assert [r.action for r in rows] == ["release.created", "release.updated"]


async def test_identical_retry_200_no_audit(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    first = await _put(client, env)
    second = await _put(client, env)
    assert second.status_code == 200
    assert second.json() == first.json()  # `updated_at` included
    assert len(await _audit(db_session, env.key)) == 1


async def test_enrich_deployed_fills_empty_fields_200_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    release = await make_release(
        db_session,
        _app(env),
        version=VERSION,
        status="deployed",
        deployed_at=datetime.now(UTC),
        created_via="deployer",
        git_sha=None,
    )
    assert release.images == [] and release.changes == []
    resp = await _put(client, env, body(deployment_pr=DEPLOY_PR, has_migrations=False))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "deployed" and data["deployed_at"] is not None
    assert data["created_via"] == "deployer" and data["git_sha"] == SHA
    assert data["has_migrations"] is False and data["deployment_pr"] == DEPLOY_PR
    assert len(data["images"]) == 1 and len(data["changes"]) == 1
    (audit,) = await _audit(db_session, env.key)
    assert audit.action == "release.updated"
    assert audit.changes["fields"]["to"] == [
        "changes",
        "deployment_pr",
        "git_sha",
        "has_migrations",
        "images",
    ]


async def test_enrich_failed_fills_empty_fields_200_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    await make_release(
        db_session,
        _app(env),
        version=VERSION,
        status="failed",
        created_via="deployer",
        git_sha=None,
    )
    resp = await _put(client, env)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "failed" and resp.json()["git_sha"] == SHA
    assert [a.action for a in await _audit(db_session, env.key)] == ["release.updated"]


async def test_enrich_conflict_409_writes_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Alembic's `fileConfig` (test DB setup) disables existing loggers.
    monkeypatch.setattr(logging.getLogger("app.modules.releases.app_write"), "disabled", False)
    env = await _env(db_session)
    assert (await _put(client, env)).status_code == 201
    await _mark(db_session, env, "deployed")
    # `has_migrations` is still null: filling it is enrichment (200) ...
    assert (await _put(client, env, body(has_migrations=True))).status_code == 200
    updated = (await _release(db_session, env)).updated_at
    audits = len(await _audit(db_session, env.key))
    # ... but a differing non-null field (the changes list) is a conflict
    other = body(has_migrations=True, changes=[{"pr_url": PR2, "title": "Other"}])
    with caplog.at_level(logging.DEBUG):
        resp = await _put(client, env, other)
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "release_not_pending"
    assert error["details"] == [{"field": "changes", "reason": "differs"}]
    assert "Other" not in resp.text and PR2 not in resp.text
    release = await _release(db_session, env)
    assert release.changes[0]["pr_url"] == PR1 and release.updated_at == updated
    assert len(await _audit(db_session, env.key)) == audits
    assert "release_not_pending_conflict" in caplog.text
    assert "Other" not in caplog.text and PR2 not in caplog.text and env.raw not in caplog.text


async def test_deployed_identical_200_noop(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    assert (await _put(client, env)).status_code == 201
    await _mark(db_session, env, "deployed")
    updated = (await _release(db_session, env)).updated_at
    resp = await _put(client, env)
    assert resp.status_code == 200
    assert (await _release(db_session, env)).updated_at == updated
    assert len(await _audit(db_session, env.key)) == 1


async def test_put_never_changes_status(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    assert (await _put(client, env)).status_code == 201
    await _mark(db_session, env, "failed")
    resp = await _put(client, env, body(has_migrations=False))
    assert resp.status_code == 200 and resp.json()["status"] == "failed"
    assert (await _release(db_session, env)).deployed_at is None


async def test_status_field_in_body_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await _put(client, env, body(status="deployed"))
    assert resp.status_code == 422
    assert await _audit(db_session, env.key) == []


async def test_staging_write(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await client.put(env.url("sha-0123456", "staging"), json=body(), headers=env.headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["environment"] == "staging"


def _app(env: Env) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(id=env.app_id)


# --- auth ----------------------------------------------------------------------


async def test_manifest_key_on_release_403_scope_denied_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session, scope="manifest")
    await db_session.commit()
    resp = await _put(client, env)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_credential_scope"
    (row,) = await _audit(db_session, env.key)
    assert (row.action, row.outcome) == ("release.created", "denied")
    assert row.changes == {"reason": "app_credential_scope"}
    assert env.raw not in str(row.changes)
    assert (
        await db_session.scalar(select(AppRelease.id).where(AppRelease.app_id == env.app_id))
    ) is None


async def test_unknown_path_app_403_mismatch(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    await db_session.commit()
    resp = await client.put(
        f"{V1}/apps/does-not-exist/releases/production/{VERSION}",
        json=body(),
        headers=env.headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_credential_mismatch"


async def test_other_app_403_mismatch_denied_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    a, b = await _env(db_session), await _env(db_session)
    await db_session.commit()
    resp = await client.put(b.url(), json=body(), headers=a.headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_credential_mismatch"
    (row,) = await _audit(db_session, a.key)
    assert (row.outcome, row.changes) == ("denied", {"reason": "app_credential_mismatch"})
    assert await _audit(db_session, b.key) == []


async def test_disabled_app_403_no_row(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session, status="disabled")
    resp = await _put(client, env)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_disabled"
    assert await _audit(db_session, env.key) == []


async def test_disabled_between_auth_and_confirm_403(db_session: AsyncSession) -> None:
    env = await _env(db_session, status="disabled")
    caller = AuthenticatedApp(uuid.uuid4(), env.key_id, env.app_id, env.key, "releases")
    from app.modules.app_credentials.errors import AppCredentialAppDisabledError

    with pytest.raises(AppCredentialAppDisabledError):
        await ReleaseAppWriteService(db_session).put(
            caller,
            ReleaseEnvironment.PRODUCTION,
            VERSION,
            ReleasePut.model_validate(body()),
            ctx=AuditContext.NONE,
        )
    assert await _audit(db_session, env.key) == []


async def test_revoked_between_auth_and_confirm_401(db_session: AsyncSession) -> None:
    key = f"app-{uuid.uuid4().hex[:8]}"
    app = await make_app(db_session, key)
    row, _ = await make_credential(db_session, app, scope="releases", revoked=True)
    caller = AuthenticatedApp(row.id, row.key_id, app.id, key, "releases")
    with pytest.raises(UnauthorizedError):
        await AppCredentialService(db_session).confirm(caller)


async def test_releases_key_for_admin_app_writes_admin_release(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.modules.rbac.repository import RbacRepository

    admin = await RbacRepository(db_session).get_app_by_key("admin")
    assert admin is not None
    row, raw = await make_credential(db_session, admin, scope="releases")
    env = Env(admin.id, "admin", row.key_id, raw)
    resp = await _put(client, env)
    assert resp.status_code == 201, resp.text
    assert resp.json()["app_key"] == "admin"


async def test_schema_invalid_body_without_credential_401(client: AsyncClient) -> None:
    resp = await client.put(
        f"{V1}/apps/stoktok/releases/production/{VERSION}", json={"status": "deployed"}
    )
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith('Bearer realm="app"')


async def test_admin_jwt_401(client: AsyncClient, db_session: AsyncSession) -> None:
    staff = await make_staff(db_session, "admin:*")
    resp = await client.put(
        f"{V1}/apps/stoktok/releases/production/{VERSION}", json=body(), headers=bearer(mint(staff))
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_deployer_key_401(client: AsyncClient) -> None:
    from app.modules.deployer_credentials.codec import new_credential

    resp = await client.put(
        f"{V1}/apps/stoktok/releases/production/{VERSION}",
        json=body(),
        headers=bearer(new_credential()[1]),
    )
    assert resp.status_code == 401


async def test_no_cors(client: AsyncClient, db_session: AsyncSession) -> None:
    admin_origin, app_origin = "https://admin-portal.mmlabs.online", "https://stoktok.mmlabs.online"
    await AppService(db_session).set_admin_origins(
        AdminOriginsSet(origins=[admin_origin]), app_env="test"
    )
    env = await _env(db_session, origins=[app_origin])
    for origin in (admin_origin, app_origin):
        resp = await client.put(
            env.url(f"v1.0.{len(origin)}"), json=body(), headers={**env.headers, "Origin": origin}
        )
        assert resp.status_code == 201
        assert "access-control-allow-origin" not in resp.headers
        pre = await client.options(
            env.url(),
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert "access-control-allow-origin" not in pre.headers


# --- validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        body(git_sha=SHA.upper()),
        body(git_sha=None),
        {k: v for k, v in body().items() if k != "git_sha"},
        {k: v for k, v in body().items() if k != "images"},
        body(images=[]),
        body(images=[{"name": "a/b:latest", "digest": DIGEST}]),
        body(images=[{"name": "a/b", "digest": "sha256:" + "A" * 64}]),
        body(changes=[{"pr_url": "https://github.com/other/x/pull/1", "title": "t"}]),
        body(changes=[{"pr_url": PR1, "title": ""}]),
        body(changes=[{"pr_url": PR1, "title": "x" * 201}]),
        body(changes=[{"pr_url": PR1, "title": "t", "ticket_id": "bad"}]),
        body(changes=[{"pr_url": PR1, "title": "t", "risk": "critical"}]),
        body(changes=[{"pr_url": PR1, "title": "a"}, {"pr_url": PR1, "title": "b"}]),
        body(deployment_pr="https://github.com/mmlabs-online/other/pull/1"),
        body(has_migrations="yes"),
        [],
        "text",
    ],
)
async def test_invalid_body_422(client: AsyncClient, db_session: AsyncSession, data: Any) -> None:
    env = await _env(db_session)
    resp = await _put(client, env, data)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"
    assert await _audit(db_session, env.key) == []


async def test_too_many_changes_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    changes = [
        {"pr_url": f"https://github.com/mmlabs-online/x/pull/{i}", "title": "t"}
        for i in range(1, 202)
    ]
    assert (await _put(client, env, body(changes=changes))).status_code == 422


@pytest.mark.parametrize(
    "version",
    ["v01.2.3", "v1.2", "v1.2.3-rc1", "1.2.3", "sha-abcdef0", "v1.2.3%0A", "v" + "1" * 40],
)
async def test_bad_version_422(client: AsyncClient, db_session: AsyncSession, version: str) -> None:
    env = await _env(db_session)
    resp = await client.put(
        f"{V1}/apps/{env.key}/releases/production/{version}", json=body(), headers=env.headers
    )
    assert resp.status_code == 422, resp.text
    assert await _audit(db_session, env.key) == []


@pytest.mark.parametrize("version", ["v1.2.3", "sha-ABCDEF0", "sha-abc", "sha-abcdef0%0A"])
async def test_bad_staging_version_422(
    client: AsyncClient, db_session: AsyncSession, version: str
) -> None:
    env = await _env(db_session)
    resp = await client.put(env.url(version, "staging"), json=body(), headers=env.headers)
    assert resp.status_code == 422, resp.text


async def test_bad_environment_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await client.put(env.url(environment="dev"), json=body(), headers=env.headers)
    assert resp.status_code == 422


async def test_staging_sha_mismatch_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await client.put(env.url("sha-abcdef0", "staging"), json=body(), headers=env.headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["loc"] == ["body", "git_sha"]


async def test_unknown_field_422_nested(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    for data in (
        body(extra=1),
        body(images=[{"name": "a/b", "digest": DIGEST, "x": 1}]),
        body(changes=[{"pr_url": PR1, "title": "t", "x": 1}]),
    ):
        assert (await _put(client, env, data)).status_code == 422


async def test_author_fields_rejected_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    for data in (
        body(author="x"),
        body(author_email="a@b.c"),
        body(changes=[{"pr_url": PR1, "title": "t", "author": "x"}]),
        body(changes=[{"pr_url": PR1, "title": "t", "author_email": "a@b.c"}]),
    ):
        assert (await _put(client, env, data)).status_code == 422
    assert await _audit(db_session, env.key) == []


async def test_malformed_json_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await client.put(
        env.url(), content=b"{not json", headers={**env.headers, "Content-Type": "application/json"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_422_details_have_no_values(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    secret = "TopSecretValue987"
    resp = await _put(
        client, env, body(git_sha=secret, deployment_pr=secret, has_migrations=secret)
    )
    assert resp.status_code == 422
    assert secret not in resp.text


async def test_missing_length_411(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)

    async def chunks() -> Any:
        yield json.dumps(body()).encode()

    resp = await client.put(
        env.url(), content=chunks(), headers={**env.headers, "Content-Type": "application/json"}
    )
    assert resp.status_code == 411
    assert resp.json()["error"]["code"] == "length_required"


async def test_body_cap_413(make_client: MakeClient, db_session: AsyncSession) -> None:
    async with make_client(app_release_max_bytes=1024) as client:
        env = await _env(db_session)
        big = body(changes=[{"pr_url": PR1, "title": "x" * 190} for _ in range(10)])
        resp = await _put(client, env, big)
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "payload_too_large"
        # A body longer than it declares is refused too.
        raw = json.dumps(big).encode()
        lie = await client.put(
            env.url(),
            content=raw,
            headers={**env.headers, "Content-Type": "application/json", "Content-Length": "900"},
        )
        assert lie.status_code in {413, 422}
    assert await _audit(db_session, env.key) == []


async def test_short_body_422(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    raw = json.dumps(body()).encode()
    resp = await client.put(
        env.url(),
        content=raw[:20],
        headers={
            **env.headers,
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == [{"field": "body", "reason": "incomplete"}]


async def test_wrong_content_type_415(client: AsyncClient, db_session: AsyncSession) -> None:
    env = await _env(db_session)
    resp = await client.put(
        env.url(),
        content=json.dumps(body()).encode(),
        headers={**env.headers, "Content-Type": "text/plain"},
    )
    assert resp.status_code == 415
    ok = await client.put(
        env.url(),
        content=json.dumps(body()).encode(),
        headers={**env.headers, "Content-Type": "application/json; charset=utf-8"},
    )
    assert ok.status_code == 201


async def test_rate_limit_429_before_auth(make_client: MakeClient) -> None:
    async with make_client(app_release_rate_limit=1) as client:
        url = f"{V1}/apps/stoktok/releases/production/{VERSION}"
        first = await client.put(url, json=body())
        second = await client.put(url, json=body())
    assert first.status_code == 401
    assert second.status_code == 429
    assert "retry-after" in second.headers


# --- audit ------------------------------------------------------------------------


async def test_audit_changes_ids_and_status_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    env = await _env(db_session)
    assert (await _put(client, env)).status_code == 201
    assert (await _put(client, env, body(has_migrations=True))).status_code == 200
    created, updated = await _audit(db_session, env.key)
    allowed = {"environment", "version", "status", "changes_count", "images_count", "fields"}
    for row in (created, updated):
        assert set(row.changes) <= allowed
        dumped = json.dumps(row.changes)
        for forbidden in ("First change", "github.com", DIGEST, SHA, "mmlabs/api"):
            assert forbidden not in dumped
    assert created.changes["status"] == {"from": None, "to": "pending"}
    assert created.changes["changes_count"] == {"from": None, "to": 1}
    assert updated.changes["fields"]["to"] == ["has_migrations"]
