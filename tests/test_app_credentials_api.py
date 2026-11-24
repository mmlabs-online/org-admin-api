import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.app_credentials.models import AppCredential
from app.modules.audit.models import AuditLog
from app.modules.rbac.models import App
from tests.app_credential_factories import make_credential
from tests.conftest import MakeClient
from tests.rbac_factories import make_app, make_user

V1 = "/api/v1"
SUPER = ["admin:*"]


def _url(key: str, credential_id: object = "") -> str:
    return f"{V1}/apps/{key}/credentials" + (f"/{credential_id}" if credential_id else "")


async def _app(db: AsyncSession, **kwargs: object) -> App:
    return await make_app(db, f"app-{uuid.uuid4().hex[:8]}", **kwargs)


async def _creds(db: AsyncSession, app_id: uuid.UUID) -> list[AppCredential]:
    rows = await db.scalars(
        select(AppCredential)
        .where(AppCredential.app_id == app_id)
        .order_by(AppCredential.created_at)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _audit(db: AsyncSession, key: str) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.app_key == key)
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def test_issue_returns_secret_once_and_stores_hash(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, actor = await _app(db_session), await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url(app.key))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert resp.headers["cache-control"] == "no-store"
        raw = body["credential"]
        assert raw.startswith("mmak_") and raw.split("_")[1] == body["key_id"]
        assert (body["active"], body["created_by"], body["app"]) == (True, str(actor.id), app.key)
        assert body["last_used_at"] is None and body["revoked_at"] is None

        (row,) = await _creds(db_session, app.id)
        secret = raw.split("_", 2)[2]
        assert row.secret_hash == hashlib.sha256(secret.encode()).digest()
        assert row.key_id == body["key_id"]
        for value in (raw, secret, row.secret_hash.hex()):  # raw value is in no column
            assert value not in f"{row.key_id} {row.created_by} {row.created_at}"

        listing = await client.get(_url(app.key))
        assert raw not in listing.text and secret not in listing.text


async def test_issue_requires_admin_wildcard(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, actor = await _app(db_session), await make_user(db_session)
    await db_session.commit()  # the denied write rolls back first: keep the setup
    key, app_id = app.key, app.id
    async with make_client(admin_permissions=["admin:apps:write"], admin_user=actor) as client:
        resp = await client.post(_url(key))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin_wildcard_required"
    assert await _creds(db_session, app_id) == []
    (row,) = await _audit(db_session, key)
    assert (row.action, row.outcome) == ("app_credential.issued", "denied")


async def test_issue_missing_route_permission_403(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await _app(db_session)
    async with make_client(admin_permissions=["admin:apps:read"]) as client:
        resp = await client.post(_url(app.key))
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "permission_denied"
    assert error["details"]["required"] == "admin:apps:write"


async def test_issue_unknown_app_404(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.post(_url("nope-app"))
    assert resp.status_code == 404


async def test_issue_admin_app_409(make_client: MakeClient, db_session: AsyncSession) -> None:
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.post(_url("admin"))
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "admin_app"


async def test_issue_disabled_app_409(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session, status="disabled")
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.post(_url(app.key))
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "app_disabled"


async def test_issue_third_active_is_409_then_rotation_works(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, actor = await _app(db_session), await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        first = await client.post(_url(app.key))
        assert (await client.post(_url(app.key))).status_code == 201
        third = await client.post(_url(app.key))
        assert third.status_code == 409
        assert third.json()["error"]["details"][0]["reason"] == "credential_limit"
        assert len(await _creds(db_session, app.id)) == 2
        assert (await client.delete(_url(app.key, first.json()["id"]))).status_code == 204
        assert (await client.post(_url(app.key))).status_code == 201


async def test_list_metadata_only(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    old, _ = await make_credential(db_session, app, revoked=True)
    new, _ = await make_credential(db_session, app)
    async with make_client(admin_permissions=["admin:apps:read"]) as client:
        resp = await client.get(_url(app.key))
    assert resp.status_code == 200
    body = resp.json()
    assert (body["total"], body["limit"], body["offset"]) == (2, 20, 0)
    assert all("credential" not in i and "hash" not in str(i) for i in body["items"])
    assert {i["key_id"]: i["active"] for i in body["items"]} == {
        old.key_id: False,
        new.key_id: True,
    }
    assert body["items"][0]["created_at"] >= body["items"][1]["created_at"]


async def test_list_unknown_app_404(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=["admin:apps:read"]) as client:
        assert (await client.get(_url("nope-app"))).status_code == 404


async def test_revoke_sets_revoked_at_and_audits(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, actor = await _app(db_session), await make_user(db_session)
    cred, _ = await make_credential(db_session, app)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.delete(_url(app.key, cred.id))
    assert resp.status_code == 204
    await db_session.refresh(cred)
    assert cred.revoked_at is not None
    (row,) = await _audit(db_session, app.key)
    assert (row.action, row.outcome, row.target_type, row.target_id) == (
        "app_credential.revoked",
        "success",
        "app_credential",
        cred.id,
    )
    assert row.actor_user_id == actor.id


async def test_revoke_twice_is_noop(make_client: MakeClient, db_session: AsyncSession) -> None:
    app = await _app(db_session)
    cred, _ = await make_credential(db_session, app)
    async with make_client(admin_permissions=SUPER) as client:
        assert (await client.delete(_url(app.key, cred.id))).status_code == 204
        await db_session.refresh(cred)
        first = cred.revoked_at
        assert (await client.delete(_url(app.key, cred.id))).status_code == 204
    await db_session.refresh(cred)
    assert cred.revoked_at == first
    assert len(await _audit(db_session, app.key)) == 1


async def test_revoke_unknown_or_foreign_credential_404(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, other = await _app(db_session), await _app(db_session)
    foreign, _ = await make_credential(db_session, other)
    async with make_client(admin_permissions=SUPER) as client:
        assert (await client.delete(_url(app.key, uuid.uuid4()))).status_code == 404
        assert (await client.delete(_url(app.key, foreign.id))).status_code == 404
    await db_session.refresh(foreign)
    assert foreign.revoked_at is None


async def test_revoke_needs_only_apps_write(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await _app(db_session)
    cred, _ = await make_credential(db_session, app)
    async with make_client(admin_permissions=["admin:apps:write"]) as client:
        assert (await client.delete(_url(app.key, cred.id))).status_code == 204


async def test_audit_rows_never_contain_credential(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, actor = await _app(db_session), await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        body = (await client.post(_url(app.key))).json()
        assert (await client.delete(_url(app.key, body["id"]))).status_code == 204
    raw = body["credential"]
    secret = raw.split("_", 2)[2]
    (cred,) = await _creds(db_session, app.id)
    rows = await _audit(db_session, app.key)
    assert sorted(r.action for r in rows) == ["app_credential.issued", "app_credential.revoked"]
    for row in rows:
        text = f"{row.changes} {row.actor_label} {row.user_agent} {row.target_type}"
        for needle in (raw, secret, cred.secret_hash.hex()):
            assert needle not in text


async def test_app_delete_cascades_credentials(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app = await _app(db_session)
    await make_credential(db_session, app)
    app_id = app.id
    async with make_client(admin_permissions=SUPER) as client:
        assert (await client.delete(f"{V1}/apps/{app.key}")).status_code == 204
    count = await db_session.scalar(
        select(func.count()).select_from(AppCredential).where(AppCredential.app_id == app_id)
    )
    assert count == 0


# --- scopes (ADM-034, ADR-0021 §1) -------------------------------------------


async def test_issue_without_body_is_manifest(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url(app.key))
    assert resp.status_code == 201
    assert resp.json()["scope"] == "manifest"
    assert (await _creds(db_session, app.id))[0].scope == "manifest"


async def test_issue_empty_object_is_manifest(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url(app.key), json={})
    assert resp.status_code == 201
    assert resp.json()["scope"] == "manifest"


async def test_issue_releases_scope(make_client: MakeClient, db_session: AsyncSession) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url(app.key), json={"scope": "releases"})
    assert resp.status_code == 201
    assert resp.json()["scope"] == "releases"
    assert (await _creds(db_session, app.id))[0].scope == "releases"


async def test_issue_unknown_scope_422(make_client: MakeClient, db_session: AsyncSession) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url(app.key), json={"scope": "bogus:scope"})
    assert resp.status_code == 422
    assert await _creds(db_session, app.id) == []


async def test_issue_extra_field_422(make_client: MakeClient, db_session: AsyncSession) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url(app.key), json={"scope": "releases", "x": 1})
    assert resp.status_code == 422


async def test_issue_releases_for_admin_app_201(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url("admin"), json={"scope": "releases"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["app"] == "admin"


async def test_issue_manifest_for_admin_app_still_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        for body in (None, {}, {"scope": "manifest"}):
            resp = await client.post(_url("admin"), json=body)
            assert resp.status_code == 409
            assert resp.json()["error"]["details"][0]["reason"] == "admin_app"


async def test_limit_is_per_scope(make_client: MakeClient, db_session: AsyncSession) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        for scope in ("manifest", "manifest", "releases", "releases"):
            assert (await client.post(_url(app.key), json={"scope": scope})).status_code == 201
        for scope in ("manifest", "releases"):
            third = await client.post(_url(app.key), json={"scope": scope})
            assert third.status_code == 409
            assert third.json()["error"]["details"][0]["reason"] == "credential_limit"
    assert len(await _creds(db_session, app.id)) == 4


async def test_issue_releases_unknown_app_404(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url("nope-app"), json={"scope": "releases"})
    assert resp.status_code == 404


async def test_list_shows_scope(make_client: MakeClient, db_session: AsyncSession) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    await make_credential(db_session, app, scope="releases")
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.get(_url(app.key))
    assert [i["scope"] for i in resp.json()["items"]] == ["releases"]


async def test_issued_audit_has_scope(make_client: MakeClient, db_session: AsyncSession) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url(app.key), json={"scope": "releases"})
    (row,) = [r for r in await _audit(db_session, app.key) if r.action == "app_credential.issued"]
    assert row.changes["scope"] == {"from": None, "to": "releases"}
    assert resp.json()["credential"] not in str(row.changes)


async def test_issue_org_members_scope(make_client: MakeClient, db_session: AsyncSession) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        for _ in range(2):
            resp = await client.post(_url(app.key), json={"scope": "org-members:read"})
            assert resp.status_code == 201, resp.text
            assert resp.json()["scope"] == "org-members:read"
        third = await client.post(_url(app.key), json={"scope": "org-members:read"})
        assert third.status_code == 409
        assert third.json()["error"]["details"][0]["reason"] == "credential_limit"
        listed = await client.get(_url(app.key))
    assert {i["scope"] for i in listed.json()["items"]} == {"org-members:read"}


async def test_org_members_scope_refused_for_admin_app(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url("admin"), json={"scope": "org-members:read"})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "admin_app"


async def test_issue_usage_read_and_write(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        for scope in ("usage:read", "usage:write"):
            resp = await client.post(_url(app.key), json={"scope": scope})
            assert resp.status_code == 201, resp.text
            assert resp.json()["scope"] == scope
            assert resp.json()["credential"].startswith("mmak_")


async def test_issue_limits_write_refused_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    """`limits:write` is in the CHECK but cannot be issued until ADM-044 (limits routes)."""
    actor = await make_user(db_session)
    app = await _app(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        resp = await client.post(_url(app.key), json={"scope": "limits:write"})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "scope_not_available"
    assert await _creds(db_session, app.id) == []


async def test_admin_app_refuses_usage_scopes(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    actor = await make_user(db_session)
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        for scope in ("usage:read", "usage:write", "limits:write"):
            resp = await client.post(_url("admin"), json={"scope": scope})
            assert resp.status_code == 409, scope
            assert resp.json()["error"]["details"][0]["reason"] == "admin_app"
