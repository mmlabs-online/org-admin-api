"""App credential authentication on the manifest route (USR-05b, ADR-0016 §1, §3, §4)."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.modules.app_credentials import service as credential_service
from app.modules.app_credentials.codec import parse_credential, secret_hash
from app.modules.app_credentials.errors import AppCredentialAppDisabledError
from app.modules.app_credentials.models import AppCredential, AppCredentialScope
from app.modules.app_credentials.schemas import AppCredentialIssued
from app.modules.app_credentials.service import AuthenticatedApp, IssuedCredential
from app.modules.audit.actions import AuditAction
from app.modules.audit.context import AuditContext
from app.modules.audit.models import AuditLog
from app.modules.rbac.manifest import ManifestService
from app.modules.rbac.models import Permission
from app.modules.rbac.schemas import ManifestPut
from tests.app_credential_factories import make_credential
from tests.conftest import MakeClient
from tests.rbac_factories import make_app

V1 = "/api/v1"
CHALLENGE = 'Bearer realm="app"'


def _url(key: str) -> str:
    return f"{V1}/apps/{key}/manifest"


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def _key() -> str:
    return f"app-{uuid.uuid4().hex[:8]}"


async def _audit_rows(session: AsyncSession) -> list[AuditLog]:
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "app.manifest_synced")
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_valid_credential_200(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, _key())
    _, raw = await make_credential(db_session, app)
    resp = await client.put(_url(app.key), json={"permissions": []}, headers=_auth(raw))
    assert resp.status_code == 200, resp.text
    assert resp.json()["app"] == app.key


async def _matrix(db_session: AsyncSession) -> dict[str, dict[str, str]]:
    """Header sets for every credential failure of ADR-0016 §4, keyed by case name."""
    app = await make_app(db_session, _key())
    row, raw = await make_credential(db_session, app)
    _, revoked_raw = await make_credential(db_session, app, revoked=True)
    return {
        "no_header": {},
        "basic": {"Authorization": "Basic Zm9vOmJhcg=="},
        "bearer_empty": {"Authorization": "Bearer "},
        "short": _auth("mmak_"),
        "uppercase_hex": _auth(raw.replace(row.key_id, row.key_id.upper())),
        "too_long": _auth(raw + "x"),
        "huge": _auth("mmak_" + "a" * 10_000),
        "jwt_shaped": _auth("eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"),
        "unknown": _auth(f"mmak_{'0' * 16}_{'A' * 43}"),
        "wrong_secret": _auth(f"mmak_{row.key_id}_{'A' * 43}"),
        "revoked": _auth(revoked_raw),
    }


async def test_all_401_answers_identical(client: AsyncClient, db_session: AsyncSession) -> None:
    bodies = []
    for name, headers in (await _matrix(db_session)).items():
        resp = await client.put(_url("stoktok"), json={"permissions": []}, headers=headers)
        assert resp.status_code == 401, name
        error = resp.json()["error"]
        assert error["code"] == "unauthorized", name
        assert error["details"] is None, name
        assert resp.headers["www-authenticate"] == CHALLENGE, name
        bodies.append(resp.json())
    assert all(b == bodies[0] for b in bodies)


async def test_wrong_app_403_and_denied_row(client: AsyncClient, db_session: AsyncSession) -> None:
    a, b = await make_app(db_session, _key()), await make_app(db_session, _key())
    row, raw = await make_credential(db_session, a)
    a_key, b_id, key_id = a.key, b.id, row.key_id
    await db_session.commit()  # the failure path rolls back first: keep the setup
    resp = await client.put(
        _url(b.key), json={"permissions": [{"key": f"{b.key}:x:read"}]}, headers=_auth(raw)
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_credential_mismatch"
    rows = await _audit_rows(db_session)
    assert len(rows) == 1
    audit = rows[0]
    assert (audit.outcome, audit.actor_kind) == ("denied", "system")
    assert audit.actor_label == f"app:{a_key}/{key_id}"
    assert audit.app_key == a_key
    stored = await db_session.scalars(select(Permission).where(Permission.app_id == b_id))
    assert list(stored) == []


async def test_wrong_app_unknown_path_app_same_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    a = await make_app(db_session, _key())
    _, raw = await make_credential(db_session, a)
    a_key = a.key
    await db_session.commit()
    resp = await client.put(_url("does-not-exist"), json={"permissions": []}, headers=_auth(raw))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_credential_mismatch"
    (audit,) = await _audit_rows(db_session)
    assert audit.app_key == a_key


async def test_disabled_app_403(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, _key(), status="disabled")
    _, raw = await make_credential(db_session, app)
    resp = await client.put(
        _url(app.key), json={"permissions": [{"key": f"{app.key}:x:read"}]}, headers=_auth(raw)
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_disabled"
    assert await _audit_rows(db_session) == []
    stored = await db_session.scalars(select(Permission).where(Permission.app_id == app.id))
    assert list(stored) == []


async def test_disabled_between_auth_and_lock_403(db_session: AsyncSession) -> None:
    app = await make_app(db_session, _key(), status="disabled")
    row, _ = await make_credential(db_session, app)
    caller = AuthenticatedApp(row.id, row.key_id, app.id, app.key, "manifest")
    with pytest.raises(AppCredentialAppDisabledError):
        await ManifestService(db_session).sync(
            app.key, ManifestPut(permissions=[]), caller=caller, ctx=AuditContext.NONE
        )


async def test_secret_compared_with_compare_digest(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = await make_app(db_session, _key())
    row, raw = await make_credential(db_session, app)
    parsed = parse_credential(raw)
    assert parsed is not None
    calls: list[tuple[Any, Any]] = []
    real = credential_service.hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append((a, b))
        return bool(real(a, b))

    monkeypatch.setattr(credential_service.hmac, "compare_digest", spy)
    resp = await client.put(_url(app.key), json={"permissions": []}, headers=_auth(raw))
    assert resp.status_code == 200
    expected = (row.secret_hash, secret_hash(parsed[1]))
    assert calls.count(expected) == 1


async def test_last_used_at_set_on_success_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, _key())
    row, raw = await make_credential(db_session, app)
    bad = _auth(f"mmak_{row.key_id}_{'A' * 43}")
    assert (
        await client.put(_url(app.key), json={"permissions": []}, headers=bad)
    ).status_code == 401
    await db_session.refresh(row)
    assert row.last_used_at is None
    resp = await client.put(_url(app.key), json={"permissions": []}, headers=_auth(raw))
    assert resp.status_code == 200
    await db_session.refresh(row)
    assert row.last_used_at is not None


async def test_rate_limit_before_auth(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, _key())
    _, raw = await make_credential(db_session, app)
    async with make_client(app_manifest_rate_limit=2) as client:
        codes = []
        for _ in range(3):
            resp = await client.put(_url(app.key), json={"permissions": []})
            codes.append(resp.status_code)
        assert codes == [401, 401, 429]
        assert int(resp.headers["retry-after"]) >= 1
        valid = await client.put(_url(app.key), json={"permissions": []}, headers=_auth(raw))
        assert valid.status_code == 429


async def test_schema_invalid_body_without_credential_is_401(client: AsyncClient) -> None:
    resp = await client.put(_url("stoktok"), json={"permissions": "nope"})
    assert resp.status_code == 401


async def test_no_secret_in_logs(
    client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    app = await make_app(db_session, _key())
    _, raw = await make_credential(db_session, app)
    parsed = parse_credential(raw)
    assert parsed is not None
    headers = await _matrix(db_session)
    headers["ok"] = _auth(raw)
    with caplog.at_level(logging.DEBUG):
        responses: list[Response] = [
            await client.put(_url(app.key), json={"permissions": []}, headers=h)
            for h in headers.values()
        ]
    assert responses[-1].status_code == 200
    assert raw not in caplog.text
    assert parsed[1] not in caplog.text
    for h in headers.values():
        value = h.get("Authorization", "").removeprefix("Bearer ")
        if value.startswith("mmak_") and len(value) < 100:
            assert value not in caplog.text


async def test_revoked_between_lookup_and_touch_401(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = await make_app(db_session, _key())
    row, raw = await make_credential(db_session, app)
    service = credential_service.AppCredentialService(db_session)
    lookup = service.repo.get_with_app

    async def revoke_after_lookup(key_id: str) -> Any:
        found = await lookup(key_id)
        await service.repo.revoke(row.id)
        return found

    monkeypatch.setattr(service.repo, "get_with_app", revoke_after_lookup)
    with pytest.raises(UnauthorizedError):
        await service.authenticate(
            raw,
            app.key,
            scope=AppCredentialScope.MANIFEST,
            denied_action=AuditAction.APP_MANIFEST_SYNCED,
            ctx=AuditContext.NONE,
        )


# --- scopes (ADM-034, ADR-0021 §1) -------------------------------------------


async def _scope_rows(session: AsyncSession) -> list[AuditLog]:
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "app.manifest_synced")
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_releases_key_on_manifest_403_scope_denied_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app = await make_app(db_session, _key())
    row, raw = await make_credential(db_session, app, scope="releases")
    key_id, key, app_id = row.key_id, app.key, app.id
    await db_session.commit()
    resp = await client.put(
        _url(key), json={"permissions": [{"key": f"{key}:x:read"}]}, headers=_auth(raw)
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_credential_scope"
    (audit,) = await _scope_rows(db_session)
    assert (audit.outcome, audit.actor_label) == ("denied", f"app:{key}/{key_id}")
    assert audit.changes == {"reason": "app_credential_scope"}
    assert raw not in str(audit.changes)
    stored = await db_session.scalars(select(Permission).where(Permission.app_id == app_id))
    assert list(stored) == []
    last_used = select(AppCredential.last_used_at).where(AppCredential.key_id == key_id)
    assert (await db_session.scalar(last_used)) is None


async def test_check_order_mismatch_before_scope(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    a, b = await make_app(db_session, _key()), await make_app(db_session, _key())
    _, raw = await make_credential(db_session, a, scope="releases")
    b_key = b.key
    await db_session.commit()
    resp = await client.put(_url(b_key), json={"permissions": []}, headers=_auth(raw))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_credential_mismatch"


async def test_disabled_before_scope(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, _key(), status="disabled")
    _, raw = await make_credential(db_session, app, scope="releases")
    resp = await client.put(_url(app.key), json={"permissions": []}, headers=_auth(raw))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_disabled"


async def test_scope_check_after_401s(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, _key())
    _, raw = await make_credential(db_session, app, scope="releases", revoked=True)
    resp = await client.put(_url(app.key), json={"permissions": []}, headers=_auth(raw))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_issued_credential_repr_hides_secret() -> None:
    issued = IssuedCredential(
        id=uuid.uuid4(),
        app="x",
        key_id="k",
        scope="manifest",
        created_by=None,
        created_at=datetime.now(UTC),
        last_used_at=None,
        revoked_at=None,
        active=True,
        credential="mmak_SECRETVALUE",
    )
    assert "SECRETVALUE" not in repr(issued)
    assert "SECRETVALUE" not in repr(AppCredentialIssued.model_validate(issued))


async def test_confirm_rechecks_status_under_lock(db_session: AsyncSession) -> None:
    app = await make_app(db_session, _key(), status="disabled")
    row, _ = await make_credential(db_session, app, scope="releases")
    caller = AuthenticatedApp(row.id, row.key_id, app.id, app.key, "releases")
    with pytest.raises(AppCredentialAppDisabledError):
        await credential_service.AppCredentialService(db_session).confirm(caller)


# --- runtime scope path (USR-29, ADR-0022 §3, ADR-0025) ---------------------------


async def _runtime_setup(db: AsyncSession) -> tuple[str, str, str, uuid.UUID]:
    from tests.rbac_factories import assign, make_org, make_role, make_user

    app = await make_app(db, _key(), org_mode="required")
    org = await make_org(db)
    role = await make_role(db, app, f"{app.key}:member")
    await assign(db, await make_user(db), role, org_id=org.id)
    row, raw = await make_credential(db, app, scope="org-members:read")
    out = (app.key, raw, row.key_id, org.id)
    await db.commit()
    return out


def _members_url(key: str, org_id: uuid.UUID) -> str:
    return f"{V1}/apps/{key}/orgs/{org_id}/members"


async def _last_used(db: AsyncSession, key_id: str) -> datetime | None:
    return await db.scalar(
        select(AppCredential.last_used_at)
        .where(AppCredential.key_id == key_id)
        .execution_options(populate_existing=True)
    )


async def test_runtime_scope_takes_no_app_lock(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.rbac.repository import RbacRepository

    key, raw, _, org_id = await _runtime_setup(db_session)

    async def _boom(self: object, key: str) -> None:
        raise AssertionError("lock_app must not run for a runtime scope")

    monkeypatch.setattr(RbacRepository, "lock_app", _boom)
    resp = await client.get(_members_url(key, org_id), headers=_auth(raw))
    assert resp.status_code == 200, resp.text


async def test_runtime_touch_throttled_60s(client: AsyncClient, db_session: AsyncSession) -> None:
    from datetime import timedelta

    from sqlalchemy import update

    key, raw, key_id, org_id = await _runtime_setup(db_session)
    assert await _last_used(db_session, key_id) is None
    await client.get(_members_url(key, org_id), headers=_auth(raw))
    first = await _last_used(db_session, key_id)
    assert first is not None
    await client.get(_members_url(key, org_id), headers=_auth(raw))
    assert await _last_used(db_session, key_id) == first
    stale = datetime.now(UTC) - timedelta(seconds=61)
    await db_session.execute(
        update(AppCredential).where(AppCredential.key_id == key_id).values(last_used_at=stale)
    )
    await db_session.commit()
    await client.get(_members_url(key, org_id), headers=_auth(raw))
    refreshed = await _last_used(db_session, key_id)
    assert refreshed is not None and refreshed > stale + timedelta(seconds=30)


async def test_revoked_runtime_key_401(client: AsyncClient, db_session: AsyncSession) -> None:
    app = await make_app(db_session, _key(), org_mode="required")
    _, raw = await make_credential(db_session, app, scope="org-members:read", revoked=True)
    key = app.key
    await db_session.commit()
    resp = await client.get(_members_url(key, uuid.uuid4()), headers=_auth(raw))
    assert resp.status_code == 401


def test_require_app_credential_refuses_lock_for_runtime_scope() -> None:
    from app.modules.app_credentials.deps import RequireAppCredential

    with pytest.raises(ValueError):
        RequireAppCredential(
            AppCredentialScope.ORG_MEMBERS_READ, AuditAction.ORGANIZATION_MEMBERS_READ, lock=True
        )
