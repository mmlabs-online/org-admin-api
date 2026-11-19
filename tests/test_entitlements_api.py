import uuid
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.etag import version_etag
from app.modules.audit.models import AuditLog
from app.modules.entitlements.repository import EntitlementRepository
from app.modules.organizations.models import Organization
from app.modules.rbac.repository import RbacRepository
from tests.conftest import MakeClient
from tests.rbac_factories import make_app, make_org, make_user

V1 = "/api/v1"
SUPER = ["admin:*"]


def _url(org_id: object, ent_id: object = "") -> str:
    return f"{V1}/organizations/{org_id}/entitlements" + (f"/{ent_id}" if ent_id else "")


async def _app(db: AsyncSession, **kwargs: object) -> Any:
    kwargs.setdefault("org_mode", "required")
    return await make_app(db, f"app-{uuid.uuid4().hex[:8]}", **kwargs)


async def _audit(db: AsyncSession, org_id: uuid.UUID) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.org_id == org_id, AuditLog.target_type == "entitlement")
        .order_by(AuditLog.occurred_at, AuditLog.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _grant(client: Any, org_id: object, app_key: str, **body: Any) -> dict[str, Any]:
    resp = await client.post(
        _url(org_id), json={"app_key": app_key, "plan": body.pop("plan", "pro"), **body}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def test_grant_returns_201_and_audit(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, app, actor = (
        await make_org(db_session),
        await _app(db_session),
        await make_user(db_session),
    )
    async with make_client(admin_permissions=SUPER, admin_user=actor) as client:
        body = await _grant(client, org.id, app.key, limits={"seats": 5})
    assert body["app_key"] == app.key
    assert (body["plan"], body["limits"], body["enabled"], body["active"]) == (
        "pro",
        {"seats": 5},
        True,
        True,
    )
    (row,) = await _audit(db_session, org.id)
    assert row.action == "entitlement.granted"
    assert (row.target_type, str(row.target_id), row.app_key) == (
        "entitlement",
        body["id"],
        app.key,
    )
    assert set(row.changes) == {"app_id", "plan", "limits", "enabled"}


async def test_grant_defaults(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        body = await _grant(client, org.id, app.key)
    assert (body["limits"], body["enabled"]) == ({}, True)


async def test_duplicate_grant_409(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        await _grant(client, org.id, app.key)
        resp = await client.post(_url(org.id), json={"app_key": app.key, "plan": "pro"})
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"][0]["reason"] == "entitlement_exists"
    assert len(await _audit(db_session, org.id)) == 1


async def test_duplicate_grant_integrity_409(
    make_client: MakeClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        await _grant(client, org.id, app.key)

        async def _none(*_: object, **__: object) -> None:
            return None

        monkeypatch.setattr(EntitlementRepository, "find", _none)
        resp = await client.post(_url(org.id), json={"app_key": app.key, "plan": "pro"})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "entitlement_exists"


async def test_unknown_org_404(make_client: MakeClient) -> None:
    missing, ent = uuid.uuid4(), uuid.uuid4()
    async with make_client(admin_permissions=SUPER) as client:
        responses = [
            await client.get(_url(missing)),
            await client.post(_url(missing), json={"app_key": "nope", "plan": "pro"}),
            await client.patch(_url(missing, ent), json={"plan": "max"}),
            await client.delete(_url(missing, ent)),
        ]
    assert [r.status_code for r in responses] == [404] * 4
    assert {r.json()["error"]["code"] for r in responses} == {"not_found"}


async def test_unknown_app_key_404(make_client: MakeClient, db_session: AsyncSession) -> None:
    org = await make_org(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.post(_url(org.id), json={"app_key": "nope-app", "plan": "pro"})
    assert resp.status_code == 404
    assert resp.json()["error"]["details"] == [{"field": "app_key", "reason": "unknown_app"}]


async def test_unknown_entitlement_404(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, other, app = await make_org(db_session), await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, other.id, app.key)
        for ent_id in (uuid.uuid4(), ent["id"]):  # a random id, and another org's id
            assert (
                await client.patch(_url(org.id, ent_id), json={"plan": "max"})
            ).status_code == 404
            assert (await client.delete(_url(org.id, ent_id))).status_code == 404


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"key": "admin"}, "admin_app"),
        ({"status": "disabled"}, "app_disabled"),
        ({"org_mode": "none"}, "org_mode_none"),
    ],
)
async def test_ungrantable_app_409(
    make_client: MakeClient, db_session: AsyncSession, kwargs: dict[str, str], reason: str
) -> None:
    org = await make_org(db_session)
    key = "admin" if "key" in kwargs else (await _app(db_session, **kwargs)).key
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.post(_url(org.id), json={"app_key": key, "plan": "pro"})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == reason


async def test_grant_locks_app_row(
    make_client: MakeClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADM-019: a grant serialises with PATCH org_mode through the app row lock."""
    keys: list[str] = []
    original = RbacRepository.lock_app

    async def spy(self: RbacRepository, key: str) -> Any:
        keys.append(key)
        return await original(self, key)

    monkeypatch.setattr(RbacRepository, "lock_app", spy)
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        await _grant(client, org.id, app.key)
        assert keys == [app.key]
        keys.clear()
        resp = await client.post(_url(org.id), json={"app_key": "admin", "plan": "pro"})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"][0]["reason"] == "admin_app"
    assert keys == []


@pytest.mark.parametrize("plan", ["", "Pro", "a" * 64, "-pro"])
async def test_plan_validation_422(
    make_client: MakeClient, db_session: AsyncSession, plan: str
) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.post(_url(org.id), json={"app_key": app.key, "plan": plan})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "limits",
    [
        {"a": -1},
        {"a": 1.5},
        {"a": True},
        {"a": "1"},
        {"a": None},
        {"a": {"b": 1}},
        {"Seats": 1},
        {"1x": 1},
        {f"k{i}": 1 for i in range(33)},
        {"a": 2**53},
    ],
)
async def test_limits_validation_422(
    make_client: MakeClient, db_session: AsyncSession, limits: dict[str, Any]
) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.post(
            _url(org.id), json={"app_key": app.key, "plan": "pro", "limits": limits}
        )
    assert resp.status_code == 422


async def test_unknown_field_422(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        created = await client.post(
            _url(org.id), json={"app_key": app.key, "plan": "pro", "limit": {}}
        )
        ent = await _grant(client, org.id, app.key)
        patched = await client.patch(_url(org.id, ent["id"]), json={"limit": {}})
    assert (created.status_code, patched.status_code) == (422, 422)


async def test_update_plan_limits_enabled(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, org.id, app.key, limits={"seats": 5})
        resp = await client.patch(
            _url(org.id, ent["id"]), json={"plan": "max", "limits": {"seats": 9}, "enabled": False}
        )
    body = resp.json()
    assert resp.status_code == 200
    assert (body["plan"], body["limits"], body["enabled"], body["active"]) == (
        "max",
        {"seats": 9},
        False,
        False,
    )
    (updated,) = [r for r in await _audit(db_session, org.id) if r.action.endswith("updated")]
    assert updated.action == "entitlement.updated"
    assert set(updated.changes) == {"plan", "limits", "enabled"}


async def test_patch_replaces_limits(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, org.id, app.key, limits={"a": 1, "b": 2})
        resp = await client.patch(_url(org.id, ent["id"]), json={"limits": {"b": 3}})
    assert resp.json()["limits"] == {"b": 3}


async def test_patch_null_is_no_change(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, org.id, app.key)
        resp = await client.patch(_url(org.id, ent["id"]), json={"plan": None})
    assert resp.status_code == 200
    assert resp.json()["plan"] == "pro"
    assert len(await _audit(db_session, org.id)) == 1


async def test_noop_patch_no_audit(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, org.id, app.key, limits={"a": 1})
        resp = await client.patch(
            _url(org.id, ent["id"]), json={"plan": "pro", "limits": {"a": 1}, "enabled": True}
        )
    assert resp.status_code == 200
    assert resp.json()["updated_at"] == ent["updated_at"]
    assert len(await _audit(db_session, org.id)) == 1


async def test_patch_on_now_disabled_app_allowed(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, org.id, app.key)
        app.status = "disabled"
        await db_session.flush()
        patched = await client.patch(_url(org.id, ent["id"]), json={"plan": "max"})
        assert patched.status_code == 200
        assert patched.json()["active"] is False
        assert (await client.delete(_url(org.id, ent["id"]))).status_code == 204


async def test_revoke_204_then_gone(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, org.id, app.key)
        assert (await client.delete(_url(org.id, ent["id"]))).status_code == 204
        assert (await client.get(_url(org.id))).json()["total"] == 0
        await _grant(client, org.id, app.key)  # re-grant is allowed
    (revoked,) = [r for r in await _audit(db_session, org.id) if r.action.endswith("revoked")]
    assert revoked.changes["plan"] == {"from": "pro", "to": None}


async def test_list_paginates_ordered_by_app_key(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, other = await make_org(db_session), await make_org(db_session)
    apps = [
        await make_app(db_session, f"zz{c}-{uuid.uuid4().hex[:6]}", org_mode="required")
        for c in "cab"
    ]
    async with make_client(admin_permissions=SUPER) as client:
        for app in apps:
            await _grant(client, org.id, app.key)
        await _grant(client, other.id, apps[0].key)
        page = (await client.get(_url(org.id), params={"limit": 2, "offset": 1})).json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 2, 1)
    assert [i["app_key"] for i in page["items"]] == sorted(a.key for a in apps)[1:]


async def _four_states(client: Any, db: AsyncSession) -> tuple[Organization, list[Any]]:
    org = await make_org(db)
    apps = [await _app(db) for _ in range(3)]
    for app in apps:
        await _grant(client, org.id, app.key, enabled=app is not apps[1])
    apps[2].status = "disabled"
    await db.flush()
    return org, apps


async def test_active_filter_definition(make_client: MakeClient, db_session: AsyncSession) -> None:
    async with make_client(admin_permissions=SUPER) as client:
        org, apps = await _four_states(client, db_session)
        everything = (await client.get(_url(org.id))).json()
        active = (await client.get(_url(org.id), params={"active": "true"})).json()
        inactive = (await client.get(_url(org.id), params={"active": "false"})).json()
    assert everything["total"] == 3
    assert [i["app_key"] for i in active["items"]] == [apps[0].key]
    assert sorted(i["app_key"] for i in inactive["items"]) == sorted([apps[1].key, apps[2].key])


async def test_active_field_matches_filter(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    async with make_client(admin_permissions=SUPER) as client:
        org, _ = await _four_states(client, db_session)
        everything = (await client.get(_url(org.id))).json()["items"]
        active = (await client.get(_url(org.id), params={"active": "true"})).json()["items"]
    assert {i["id"] for i in everything if i["active"]} == {i["id"] for i in active}


async def test_suspended_org_behaviour(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, app, app2 = await make_org(db_session), await _app(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        first = await _grant(client, org.id, app.key)
        org.status = "suspended"
        await db_session.flush()
        second = await _grant(client, org.id, app2.key)  # grant still allowed
        assert (
            await client.patch(_url(org.id, first["id"]), json={"plan": "max"})
        ).status_code == 200
        items = (await client.get(_url(org.id))).json()["items"]
        assert [i["active"] for i in items] == [False, False]
        active = await client.get(_url(org.id), params={"active": "true"})
        assert active.json()["total"] == 0
        assert (await client.delete(_url(org.id, second["id"]))).status_code == 204
        org.status = "active"
        await db_session.flush()
        items = (await client.get(_url(org.id), params={"active": "true"})).json()["items"]
        assert [i["id"] for i in items] == [first["id"]]


async def test_read_permission_cannot_write(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, org.id, app.key)
    async with make_client(admin_permissions=["admin:entitlements:read"]) as client:
        assert (await client.get(_url(org.id))).status_code == 200
        writes = [
            await client.post(_url(org.id), json={"app_key": app.key, "plan": "pro"}),
            await client.patch(_url(org.id, ent["id"]), json={"plan": "max"}),
            await client.delete(_url(org.id, ent["id"])),
        ]
    for resp in writes:
        assert resp.status_code == 403
        error = resp.json()["error"]
        assert error["code"] == "permission_denied"
        assert error["details"]["required"] == "admin:entitlements:write"


async def test_org_delete_with_entitlements_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, org.id, app.key)
        resp = await client.delete(f"{V1}/organizations/{org.id}")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "org_has_entitlements"
        await client.delete(_url(org.id, ent["id"]))
        assert (await client.delete(f"{V1}/organizations/{org.id}")).status_code == 204


async def test_app_delete_with_entitlements_409(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        await _grant(client, org.id, app.key)
        resp = await client.delete(f"{V1}/apps/{app.key}")
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"][0]["reason"] == "app_has_entitlements"


# --- ADM-024: ETag / If-Match -------------------------------------------------------------


def _tag(body: dict[str, Any]) -> str:
    return version_etag(datetime.fromisoformat(body["updated_at"]))


async def _updated_audits(db: AsyncSession, org_id: uuid.UUID) -> int:
    return len([r for r in await _audit(db, org_id) if r.action == "entitlement.updated"])


async def _setup(
    make_client: MakeClient, db: AsyncSession, **grant: Any
) -> tuple[Any, Any, dict[str, Any]]:
    org, app = await make_org(db), await _app(db)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, org.id, app.key, **grant)
    return org, app, ent


async def test_grant_entitlement_returns_etag(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, app = await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.post(_url(org.id), json={"app_key": app.key, "plan": "pro"})
    assert resp.status_code == 201
    assert resp.headers["etag"] == _tag(resp.json())


async def test_get_entitlement_returns_etag(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session)
    async with make_client(admin_permissions=["admin:entitlements:read"]) as client:
        resp = await client.get(_url(org.id, ent["id"]))
        listed = (await client.get(_url(org.id))).json()["items"]
    assert resp.status_code == 200
    assert resp.json() == listed[0]
    assert resp.headers["etag"] == _tag(resp.json())


async def test_get_entitlement_404s(make_client: MakeClient, db_session: AsyncSession) -> None:
    org, other, app = await make_org(db_session), await make_org(db_session), await _app(db_session)
    async with make_client(admin_permissions=SUPER) as client:
        ent = await _grant(client, other.id, app.key)
        for org_id, ent_id in (
            (uuid.uuid4(), ent["id"]),  # unknown org
            (org.id, uuid.uuid4()),  # unknown entitlement
            (org.id, ent["id"]),  # another org's entitlement
        ):
            resp = await client.get(_url(org_id, ent_id))
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "not_found"


async def test_get_entitlement_401_and_403(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session)
    async with make_client(admin_permissions=["admin:organizations:read"]) as client:
        resp = await client.get(_url(org.id, ent["id"]))
    assert resp.status_code == 403
    assert resp.json()["error"]["details"]["required"] == "admin:entitlements:read"
    async with make_client() as client:
        assert (await client.get(_url(org.id, ent["id"]))).status_code == 401


async def test_patch_entitlement_returns_etag(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(_url(org.id, ent["id"]), json={"plan": "max"})
    assert resp.status_code == 200
    assert resp.headers["etag"] == _tag(resp.json())


async def test_patch_entitlement_matching_if_match_changes_etag(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session, limits={"seats": 5})
    async with make_client(admin_permissions=SUPER) as client:
        url = _url(org.id, ent["id"])
        a = (await client.get(url)).headers["etag"]
        resp = await client.patch(url, json={"limits": {"seats": 9}}, headers={"If-Match": a})
    assert resp.status_code == 200
    assert resp.json()["limits"] == {"seats": 9}
    assert resp.headers["etag"] not in {a, _tag(ent)}
    assert resp.headers["etag"] == _tag(resp.json())
    assert await _updated_audits(db_session, org.id) == 1


async def test_patch_entitlement_stale_if_match_412(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session, limits={"seats": 5})
    async with make_client(admin_permissions=SUPER) as client:
        url = _url(org.id, ent["id"])
        a = (await client.get(url)).headers["etag"]  # both clients hold A
        first = await client.patch(url, json={"limits": {"seats": 9}}, headers={"If-Match": a})
        assert first.status_code == 200
        audits = await _updated_audits(db_session, org.id)
        second = await client.patch(url, json={"limits": {"api": 1}}, headers={"If-Match": a})
        assert second.status_code == 412
        error = second.json()["error"]
        assert (error["code"], error["details"]) == ("precondition_failed", None)
        assert second.headers["etag"] == first.headers["etag"]
        assert (await client.get(url)).json()["limits"] == {"seats": 9}
    assert await _updated_audits(db_session, org.id) == audits


async def test_patch_entitlement_if_match_star(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(
            _url(org.id, ent["id"]), json={"plan": "max"}, headers={"If-Match": "*"}
        )
    assert resp.status_code == 200
    assert resp.json()["plan"] == "max"


async def test_patch_entitlement_weak_if_match_412(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(
            _url(org.id, ent["id"]),
            json={"plan": "max"},
            headers={"If-Match": f"W/{_tag(ent)}"},
        )
    assert resp.status_code == 412


async def test_patch_entitlement_noop_with_matching_if_match(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session, limits={"a": 1})
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(
            _url(org.id, ent["id"]),
            json={"plan": "pro", "limits": {"a": 1}},
            headers={"If-Match": _tag(ent)},
        )
    assert resp.status_code == 200
    assert resp.headers["etag"] == _tag(ent)
    assert await _updated_audits(db_session, org.id) == 0


async def test_patch_entitlement_without_if_match_unchanged(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(_url(org.id, ent["id"]), json={"plan": "max"})
    assert resp.status_code == 200
    assert resp.headers["etag"] == _tag(resp.json())
    assert await _updated_audits(db_session, org.id) == 1


async def test_patch_entitlement_404_beats_412_and_403_unchanged(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    org, _, ent = await _setup(make_client, db_session)
    async with make_client(admin_permissions=SUPER) as client:
        resp = await client.patch(
            _url(org.id, uuid.uuid4()), json={"plan": "max"}, headers={"If-Match": '"1"'}
        )
    assert resp.status_code == 404
    async with make_client(admin_permissions=["admin:entitlements:read"]) as client:
        resp = await client.patch(
            _url(org.id, ent["id"]), json={"plan": "max"}, headers={"If-Match": '"1"'}
        )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "permission_denied"
