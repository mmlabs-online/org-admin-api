"""GET /api/v1/audit-logs/export (ADM-022): CSV, filters, cap, audit row, auth."""

import csv
import io
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit_service
from app.modules.audit.models import AuditLog
from app.modules.audit.schemas import AuditLogRead
from app.modules.audit.service import AuditLogger
from app.modules.users.models import User
from tests.conftest import MakeClient
from tests.rbac_factories import make_user

pytestmark = pytest.mark.backdate_audit

URL = "/api/v1/audit-logs/export"
LIST = "/api/v1/audit-logs"
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


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


def _parse(resp: Response) -> list[dict[str, str]]:
    assert resp.status_code == 200, resp.text
    return list(csv.DictReader(io.StringIO(resp.text, newline="")))


def _ids(resp: Response) -> list[str]:
    return [r["id"] for r in _parse(resp)]


async def _exports(db_session: AsyncSession) -> list[AuditLog]:
    result = await db_session.scalars(
        select(AuditLog)
        .where(AuditLog.action == "audit_log.exported")
        .execution_options(populate_existing=True)
    )
    return list(result)


@pytest.fixture
async def actor(db_session: AsyncSession) -> User:
    return await make_user(db_session)


async def test_export_happy_path_with_filters(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    org, who = uuid.uuid4(), uuid.uuid4()
    hit = [
        await _add(db_session, m, org_id=org, actor_user_id=who, action="user.created")
        for m in (5, 7, 6)
    ]
    await _add(db_session, 6, org_id=uuid.uuid4(), actor_user_id=who, action="user.created")
    await _add(db_session, 6, org_id=org, actor_user_id=who, action="user.disabled")
    await _add(db_session, 1, org_id=org, actor_user_id=who, action="user.created")  # too old
    await _add(db_session, 20, org_id=org, actor_user_id=who, action="user.created")  # too new
    resp = await admin_client.get(
        URL,
        params={
            "org_id": str(org),
            "action": "user.created",
            "from": (T0 + timedelta(minutes=2)).isoformat(),
            "to": (T0 + timedelta(minutes=10)).isoformat(),
        },
    )
    expected = [str(r.id) for r in sorted(hit, key=lambda r: r.occurred_at, reverse=True)]
    assert _ids(resp) == expected


async def test_export_filters_match_list(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    org, who = uuid.uuid4(), uuid.uuid4()
    await _add(db_session, 1, org_id=org, actor_user_id=who)
    await _add(db_session, 2, org_id=org, outcome="failure", app_key="stoktok")
    await _add(db_session, 3, target_type="organization", target_id=org)
    await _add(db_session, 4, actor_user_id=who, action="user.created")
    for params in (
        {"org_id": str(org)},
        {"actor_user_id": str(who)},
        {"outcome": "failure", "app_key": "stoktok"},
        {"target_type": "organization", "target_id": str(org)},
        {"action": "user.created", "from": (T0 + timedelta(minutes=1)).isoformat()},
    ):
        listed = await admin_client.get(LIST, params={**params, "limit": 100})
        assert _ids(await admin_client.get(URL, params=params)) == [
            i["id"] for i in listed.json()["items"]
        ], params


async def test_export_cap_truncates_newest_first(
    admin_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_service, "EXPORT_MAX_ROWS", 3)
    who = uuid.uuid4()
    rows = [await _add(db_session, m, actor_user_id=who) for m in range(4)]
    resp = await admin_client.get(URL, params={"actor_user_id": str(who)})
    assert _ids(resp) == [str(r.id) for r in reversed(rows[1:])]
    assert resp.headers["x-export-truncated"] == "true"
    assert resp.headers["x-export-row-count"] == "3"


async def test_export_exact_cap_not_truncated(
    admin_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_service, "EXPORT_MAX_ROWS", 3)
    who = uuid.uuid4()
    for m in range(3):
        await _add(db_session, m, actor_user_id=who)
    resp = await admin_client.get(URL, params={"actor_user_id": str(who)})
    assert len(_parse(resp)) == 3
    assert resp.headers["x-export-truncated"] == "false"
    assert resp.headers["x-export-row-count"] == "3"


def test_export_cap_constant_is_10000() -> None:
    assert audit_service.EXPORT_MAX_ROWS == 10_000


async def test_export_empty_is_header_only(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(URL, params={"actor_user_id": str(uuid.uuid4())})
    assert resp.status_code == 200
    assert resp.text == ",".join(AuditLogRead.model_fields) + "\r\n"
    assert resp.headers["x-export-truncated"] == "false"
    assert resp.headers["x-export-row-count"] == "0"


async def test_export_headers(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert re.fullmatch(
        r'attachment; filename="audit-log-\d{8}T\d{6}Z\.csv"', resp.headers["content-disposition"]
    )
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-request-id"]
    assert resp.text.startswith("id,")


async def test_export_columns_equal_read_schema(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    who = uuid.uuid4()
    await _add(
        db_session,
        actor_user_id=who,
        target_type="organization",
        target_id=uuid.uuid4(),
        changes={"name": {"from": "a", "to": "b"}},
        ip="203.0.113.5",
        user_agent="ua/1.0",
        request_id="req-1",
    )
    params = {"actor_user_id": str(who)}
    resp = await admin_client.get(URL, params=params)
    assert resp.text.splitlines()[0].split(",") == list(AuditLogRead.model_fields)
    listed = (await admin_client.get(LIST, params=params)).json()["items"][0]
    cells = _parse(resp)[0]
    for key, value in listed.items():
        expected = (
            ""
            if value is None
            else json.dumps(value, sort_keys=True, separators=(",", ":"))
            if isinstance(value, dict)
            else str(value)
        )
        assert cells[key] == expected, key


async def test_export_formula_injection_escaped(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    who = uuid.uuid4()
    await _add(db_session, 1, actor_user_id=who, user_agent='=HYPERLINK("x")')
    await _add(db_session, 2, actor_user_id=who, changes={"name": {"from": None, "to": "@SUM(1)"}})
    rows = _parse(await admin_client.get(URL, params={"actor_user_id": str(who)}))
    changed, ua = rows
    assert ua["user_agent"] == '\'=HYPERLINK("x")'
    assert changed["changes"].startswith("{")
    assert json.loads(changed["changes"]) == {"name": {"from": None, "to": "@SUM(1)"}}


async def test_export_writes_one_audit_row(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    who, org = uuid.uuid4(), uuid.uuid4()
    row = await _add(db_session, 1, actor_user_id=who, org_id=org, user_agent="row-ua")
    async with make_client(admin_permissions=["admin:audit:read"], admin_user=actor) as client:
        resp = await client.get(
            URL,
            headers={"User-Agent": "export-ua"},
            params={"actor_user_id": str(who), "org_id": str(org)},
        )
    assert len(_parse(resp)) == 1
    (entry,) = await _exports(db_session)
    assert (entry.outcome, entry.actor_kind, entry.actor_user_id) == ("success", "user", actor.id)
    assert (entry.target_type, entry.target_id, entry.org_id, entry.app_key) == (None,) * 4
    assert entry.user_agent == "export-ua"
    assert entry.changes == {
        "row_count": 1,
        "truncated": False,
        "max_rows": 10_000,
        "actor_user_id": str(who),
        "org_id": str(org),
    }
    assert str(row.id) not in json.dumps(entry.changes)


async def test_export_row_not_in_its_own_file(
    make_client: MakeClient, db_session: AsyncSession, actor: User
) -> None:
    async with make_client(admin_permissions=["admin:audit:read"], admin_user=actor) as client:
        first = await client.get(URL)
        (entry,) = await _exports(db_session)
        assert str(entry.id) not in _ids(first)
        assert str(entry.id) in _ids(await client.get(URL))


async def test_export_audit_failure_returns_no_file(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    actor: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _add(db_session, 1)
    await db_session.commit()

    async def boom(self: AuditLogger, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(AuditLogger, "record", boom)
    before = await db_session.scalar(select(func.count()).select_from(AuditLog))
    async with make_strict_client(
        admin_permissions=["admin:audit:read"], admin_user=actor
    ) as client:
        client._transport.raise_app_exceptions = False  # type: ignore[attr-defined]
        resp = await client.get(URL)
    assert resp.status_code == 500
    assert "text/csv" not in resp.headers["content-type"]
    assert not await _exports(db_session)
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == before


async def test_export_401_without_token(client: AsyncClient) -> None:
    resp = await client.get(URL)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_export_403_without_permission(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    async with make_client(admin_permissions=["admin:users:read"]) as client:
        resp = await client.get(URL)
    assert resp.status_code == 403
    assert resp.json()["error"]["details"] == {"required": "admin:audit:read"}
    assert not await _exports(db_session)


async def test_export_422_from_after_to_writes_no_row(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await admin_client.get(
        URL, params={"from": T0.isoformat(), "to": (T0 - timedelta(days=1)).isoformat()}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unprocessable"
    assert resp.json()["error"]["details"][0]["field"] == "from"
    assert (await admin_client.get(URL, params={"outcome": "nope"})).status_code == 422
    assert not await _exports(db_session)


async def test_export_route_not_captured_by_id(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")


async def test_export_ignores_limit_offset(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    who = uuid.uuid4()
    for m in range(3):
        await _add(db_session, m, actor_user_id=who)
    resp = await admin_client.get(URL, params={"actor_user_id": str(who), "limit": 1, "offset": 5})
    assert len(_parse(resp)) == 3
