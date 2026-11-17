"""App credential authentication on the manifest route (USR-05b, ADR-0016 §1, §3, §4)."""

import logging
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.modules.app_credentials import service as credential_service
from app.modules.app_credentials.codec import parse_credential, secret_hash
from app.modules.app_credentials.errors import AppCredentialAppDisabledError
from app.modules.app_credentials.service import AuthenticatedApp
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
    caller = AuthenticatedApp(row.id, row.key_id, app.id, app.key)
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
        await service.authenticate(raw, app.key, ctx=AuditContext.NONE)
