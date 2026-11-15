"""GET /api/v1/audit-logs (ADM-004): order, paging, filters, validation, auth."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.rbac.models import Permission, RolePermission
from tests.conftest import MakeClient

URL = "/api/v1/audit-logs"
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
READ_KEYS = {
    "id",
    "occurred_at",
    "actor_kind",
    "actor_user_id",
    "actor_label",
    "action",
    "outcome",
    "target_type",
    "target_id",
    "app_key",
    "org_id",
    "changes",
    "ip",
    "user_agent",
    "request_id",
}


async def _add(db_session: AsyncSession, minutes: int = 0, **kwargs: Any) -> AuditLog:
    values: dict[str, Any] = {
        "actor_kind": "user",
        "actor_user_id": uuid.uuid4(),
        "action": "organization.created",
        "occurred_at": T0 + timedelta(minutes=minutes),
    }
    values.update(kwargs)
    row = AuditLog(**values)
    db_session.add(row)
    await db_session.flush()
    return row


def _ids(resp: Any) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [item["id"] for item in resp.json()["items"]]


async def test_list_newest_first_paged(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    rows = [await _add(db_session, minutes=m) for m in (0, 2, 1)]
    tie = await _add(db_session, minutes=2)
    expected = [
        str(r.id) for r in sorted([*rows, tie], key=lambda r: (r.occurred_at, r.id), reverse=True)
    ]

    resp = await admin_client.get(URL)
    assert _ids(resp) == expected
    assert resp.json()["total"] == 4
    assert set(resp.json()["items"][0]) == READ_KEYS

    page = await admin_client.get(URL, params={"limit": 2, "offset": 1})
    assert _ids(page) == expected[1:3]
    assert {k: page.json()[k] for k in ("total", "limit", "offset")} == {
        "total": 4,
        "limit": 2,
        "offset": 1,
    }


async def test_filter_actor_user_id(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    mine = await _add(db_session)
    await _add(db_session)
    resp = await admin_client.get(URL, params={"actor_user_id": str(mine.actor_user_id)})
    assert _ids(resp) == [str(mine.id)]


async def test_filter_actor_kind(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    await _add(db_session)
    cli = await _add(db_session, actor_kind="system", actor_user_id=None, actor_label="system:cli")
    resp = await admin_client.get(URL, params={"actor_kind": "system"})
    assert _ids(resp) == [str(cli.id)]
    assert resp.json()["items"][0]["actor_label"] == "system:cli"


async def test_filter_action(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    await _add(db_session)
    deleted = await _add(db_session, action="organization.deleted")
    assert _ids(await admin_client.get(URL, params={"action": "organization.deleted"})) == [
        str(deleted.id)
    ]


async def test_filter_target(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    target = uuid.uuid4()
    hit = await _add(db_session, target_type="app", target_id=target)
    await _add(db_session, target_type="role", target_id=target)
    await _add(db_session, target_type="app", target_id=uuid.uuid4())
    resp = await admin_client.get(URL, params={"target_type": "app", "target_id": str(target)})
    assert _ids(resp) == [str(hit.id)]
    assert len(_ids(await admin_client.get(URL, params={"target_id": str(target)}))) == 2


async def test_filter_app_key(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    hit = await _add(db_session, app_key="stoktok", ip="203.0.113.7")
    await _add(db_session, app_key="ricotdin")
    resp = await admin_client.get(URL, params={"app_key": "stoktok"})
    assert _ids(resp) == [str(hit.id)]
    assert resp.json()["items"][0]["ip"] == "203.0.113.7"


async def test_time_range_half_open(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    before, start, inside, end = [await _add(db_session, minutes=m) for m in (-1, 0, 5, 10)]
    params = {"from": T0.isoformat(), "to": (T0 + timedelta(minutes=10)).isoformat()}
    assert _ids(await admin_client.get(URL, params=params)) == [str(inside.id), str(start.id)]
    only_from = await admin_client.get(URL, params={"from": params["to"]})
    assert _ids(only_from) == [str(end.id)]
    assert _ids(await admin_client.get(URL, params={"to": params["from"]})) == [str(before.id)]


async def test_unknown_app_key_empty_page(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _add(db_session, app_key="stoktok")
    resp = await admin_client.get(URL, params={"app_key": "gone-app"})
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-11-01T12:00:00"},
        {"to": "2026-11-01T12:00:00"},
        {"action": "organization.exploded"},
        {"actor_kind": "robot"},
        {"target_type": "galaxy"},
        {"actor_user_id": "not-a-uuid"},
        {"app_key": "Bad Key"},
        {"limit": 101},
        {"offset": -1},
    ],
    ids=["naive_from", "naive_to", "action", "actor_kind", "target", "uuid", "app", "limit", "off"],
)
async def test_422_validation(admin_client: AsyncClient, params: dict[str, Any]) -> None:
    """Covers test_422_naive_datetime, test_422_unknown_action and test_422_limit_over_100."""
    resp = await admin_client.get(URL, params=params)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_422_from_after_to(admin_client: AsyncClient) -> None:
    params = {"from": (T0 + timedelta(seconds=1)).isoformat(), "to": T0.isoformat()}
    resp = await admin_client.get(URL, params=params)
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "unprocessable"
    assert error["details"] == [{"field": "from", "reason": "after_to"}]


async def test_outcome_field_present(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    await _add(db_session)
    await _add(db_session, minutes=1, outcome="denied")
    items = (await admin_client.get(URL)).json()["items"]
    assert [i["outcome"] for i in items] == ["denied", "success"]


async def test_filter_by_outcome(admin_client: AsyncClient, db_session: AsyncSession) -> None:
    await _add(db_session)
    failed = await _add(
        db_session,
        minutes=1,
        outcome="failure",
        actor_kind="anonymous",
        actor_user_id=None,
        action="auth.login_failed",
    )
    denied = await _add(db_session, minutes=2, outcome="denied")
    assert _ids(await admin_client.get(URL, params={"outcome": "failure"})) == [str(failed.id)]
    assert _ids(await admin_client.get(URL, params={"outcome": "denied"})) == [str(denied.id)]
    assert len(_ids(await admin_client.get(URL, params={"outcome": "success"}))) == 1
    both = await admin_client.get(
        URL, params={"outcome": "failure", "action": "auth.login_failed", "actor_kind": "anonymous"}
    )
    assert _ids(both) == [str(failed.id)]


async def test_invalid_outcome_422(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(URL, params={"outcome": "maybe"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_401_without_token(client: AsyncClient) -> None:
    resp = await client.get(URL)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_403_without_permission(make_client: MakeClient) -> None:
    async with make_client(admin_permissions=["admin:users:read"]) as client:
        resp = await client.get(URL)
    assert resp.status_code == 403
    assert resp.json()["error"] == {
        "code": "permission_denied",
        "message": "Missing permission",
        "details": {"required": "admin:audit:read"},
    }
    async with make_client(admin_permissions=["admin:audit:read"]) as client:
        assert (await client.get(URL)).status_code == 200


async def test_permission_seeded_not_granted(db_session: AsyncSession) -> None:
    perm = await db_session.scalar(select(Permission).where(Permission.key == "admin:audit:read"))
    assert perm is not None
    assert perm.is_admin is True
    assert str(perm.app_id) == "00000000-0000-4000-8000-0000000000a1"
    granted = await db_session.scalar(
        select(RolePermission).where(RolePermission.permission_id == perm.id)
    )
    assert granted is None
