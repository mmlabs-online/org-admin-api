"""ADM-035: staff issue, list and revoke of deployer credentials (ADR-0021 §2, §9)."""

import hashlib
import logging
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.modules.audit.models import AuditLog
from app.modules.deployer_credentials.models import DeployerCredential
from app.modules.deployer_credentials.service import DeployerCredentialService
from tests.conftest import MakeClient
from tests.deployer_credential_factories import make_deployer_credential
from tests.rbac_factories import make_user

URL = "/api/v1/deployer-credentials"
SUPER = ["admin:*"]


async def _rows(db: AsyncSession) -> list[DeployerCredential]:
    rows = await db.scalars(
        select(DeployerCredential)
        .order_by(DeployerCredential.created_at)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _audit(db: AsyncSession) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.action.like("deployer_credential.%"))
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_issue_list_revoke_happy_path(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        created = await client.post(URL)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["credential"].startswith("mmdk_")
        assert (body["active"], body["created_by"]) == (True, str(actor.id))
        assert body["last_used_at"] is None and body["revoked_at"] is None

        listing = (await client.get(URL)).json()
        assert listing["total"] == 1
        (item,) = listing["items"]
        assert item["id"] == body["id"] and item["active"] is True
        assert "credential" not in item

        assert (await client.delete(f"{URL}/{body['id']}")).status_code == 204
        (item,) = (await client.get(URL)).json()["items"]
        assert item["active"] is False and item["revoked_at"] is not None


async def test_issue_returns_value_once_no_store(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(URL)
        assert resp.headers["cache-control"] == "no-store"
        raw = resp.json()["credential"]
        secret = raw.split("_", 2)[2]
        (row,) = await _rows(db_session)
        assert row.secret_hash == hashlib.sha256(secret.encode()).digest()
        assert row.secret_hash.hex() not in repr(row) and secret not in repr(row)
        listing = await client.get(URL)
        assert raw not in listing.text and secret not in listing.text
        for item in listing.json()["items"]:
            assert not [k for k in item if "hash" in k or k == "credential"]


async def test_third_active_key_409(make_client: MakeClient, db_session: AsyncSession) -> None:
    for _ in range(2):
        await make_deployer_credential(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.post(URL)
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"][0]["reason"] == "credential_limit"
    assert len(await _rows(db_session)) == 2


async def test_revoked_keys_do_not_count(make_client: MakeClient, db_session: AsyncSession) -> None:
    await make_deployer_credential(db_session)
    await make_deployer_credential(db_session, revoked=True)
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        assert (await client.post(URL)).status_code == 201


async def test_revoke_then_issue(make_client: MakeClient, db_session: AsyncSession) -> None:
    row, _ = await make_deployer_credential(db_session)
    await make_deployer_credential(db_session)
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        assert (await client.post(URL)).status_code == 409
        assert (await client.delete(f"{URL}/{row.id}")).status_code == 204
        assert (await client.post(URL)).status_code == 201


async def test_revoke_twice_is_noop(make_client: MakeClient, db_session: AsyncSession) -> None:
    row, _ = await make_deployer_credential(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=await make_user(db_session)) as c:
        assert (await c.delete(f"{URL}/{row.id}")).status_code == 204
        assert (await c.delete(f"{URL}/{row.id}")).status_code == 204
    assert [a.action for a in await _audit(db_session)] == ["deployer_credential.revoked"]


async def test_revoke_unknown_404(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.delete(f"{URL}/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_revoke_non_uuid_422(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=SUPER) as client:
        assert (await client.delete(f"{URL}/nope")).status_code == 422


async def test_revoke_last_key_allowed(make_client: MakeClient, db_session: AsyncSession) -> None:
    row, _ = await make_deployer_credential(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        assert (await client.delete(f"{URL}/{row.id}")).status_code == 204


async def test_list_pagination_and_order(make_client: MakeClient, db_session: AsyncSession) -> None:
    first, _ = await make_deployer_credential(db_session, revoked=True)
    second, _ = await make_deployer_credential(db_session)
    third, _ = await make_deployer_credential(db_session)
    ordered = sorted([first, second, third], key=lambda r: (-r.created_at.timestamp(), r.id))
    async with make_client(admin_permissions=SUPER) as client:
        full = (await client.get(URL)).json()
        assert [i["id"] for i in full["items"]] == [str(r.id) for r in ordered]
        assert full["total"] == 3
        page = (await client.get(URL, params={"limit": 1, "offset": 1})).json()
        assert [i["id"] for i in page["items"]] == [str(ordered[1].id)]
        assert (await client.get(URL, params={"limit": 101})).status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", URL), ("GET", URL), ("DELETE", f"{URL}/{uuid.uuid4()}")],
)
async def test_routes_require_admin_wildcard(
    make_client: MakeClient, method: str, path: str
) -> None:
    async with make_client(admin_permissions=["admin:apps:write", "admin:apps:read"]) as client:
        resp = await client.request(method, path)
    assert resp.status_code == 403
    assert resp.json()["error"]["details"] == {"required": "admin:*"}


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", URL), ("GET", URL), ("DELETE", f"{URL}/{uuid.uuid4()}")],
)
async def test_routes_401_without_token(make_client: MakeClient, method: str, path: str) -> None:
    async with make_client() as client:
        resp = await client.request(method, path)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith('Bearer realm="admin"')


async def test_audit_rows_hold_key_id_only(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        body = (await client.post(URL)).json()
        await client.delete(f"{URL}/{body['id']}")
    issued, revoked = sorted(await _audit(db_session), key=lambda a: a.action)
    assert issued.action == "deployer_credential.issued"
    assert issued.changes == {"key_id": {"from": None, "to": body["key_id"]}}
    assert (issued.target_type, str(issued.target_id)) == ("deployer_credential", body["id"])
    assert issued.app_key is None and issued.actor_user_id == actor.id
    assert set(revoked.changes or {}) == {"revoked_at"}


async def test_value_never_logged_or_audited(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("app.modules.deployer_credentials.service")
    handler, level, disabled = Capture(), logger.level, logger.disabled
    logger.disabled = False  # Alembic's `fileConfig` (test session setup) disables app loggers
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        actor = await make_user(db_session)
        async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
            body = (await client.post(URL)).json()
        raw = body["credential"]
        secret = raw.split("_", 2)[2]
        wrong = f"mmdk_{body['key_id']}_{'A' * 43}"
        with pytest.raises(UnauthorizedError):
            await DeployerCredentialService(db_session).authenticate(wrong)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.disabled = disabled
    assert records == [f"deployer_credential_denied reason=secret key_id={body['key_id']}"]
    text = " ".join(records)
    assert raw not in text and secret not in text
    dump = " ".join(f"{a.changes} {a.target_id}" for a in await _audit(db_session))
    assert raw not in dump and secret not in dump


async def test_list_shows_last_used_at(make_client: MakeClient, db_session: AsyncSession) -> None:
    _, raw = await make_deployer_credential(db_session)
    await DeployerCredentialService(db_session).authenticate(raw)
    db_session.expire_all()  # the touch is a bulk UPDATE; the list must re-read the row
    async with make_client(admin_permissions=SUPER) as client:
        (item,) = (await client.get(URL)).json()["items"]
    assert item["last_used_at"] is not None
