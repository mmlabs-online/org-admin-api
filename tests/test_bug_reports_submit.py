"""POST /api/v1/auth/bug-reports (ADM-028): submit, refusals, validation, caps, audit, CORS."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.audit.service import AuditLogger
from app.modules.bug_reports import service
from app.modules.bug_reports.models import BugReport
from app.modules.rbac.models import App, Role
from app.modules.sessions.models import AccountSession
from app.modules.users.models import User
from tests.auth_factories import (
    ORIGIN,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    parse_set_cookie,
    shift,
)
from tests.conftest import MakeClient
from tests.rbac_factories import assign, make_org, make_role

URL = "/api/v1/auth/bug-reports"
NO_BURST = {"bug_report_user_rate_limit": 1000}
OTHER_ORIGIN = "https://calechip.mmlabs.online"


def _error(resp: Any) -> dict[str, Any]:
    error: dict[str, Any] = resp.json()["error"]
    return error


def _headers(raw: str, app_key: str = "ricotdin", origin: str = ORIGIN) -> dict[str, str]:
    return auth_headers(app_key=app_key, origin=origin, cookie=raw)


def _body(account: AccountSession, **extra: Any) -> dict[str, Any]:
    return {"account_id": str(account.id), "description": "The save button does nothing", **extra}


async def _app(db: AsyncSession, key: str = "ricotdin", **kwargs: Any) -> tuple[App, Role]:
    app = await make_login_app(db, key, **kwargs)
    role = await make_role(db, app, f"{key}:member")
    app.default_role_id = role.id
    await db.flush()
    return app, role


async def _member(
    db: AsyncSession, role: Role | None, **user_kwargs: Any
) -> tuple[User, str, AccountSession]:
    user = await make_login_user(db, **user_kwargs)
    if role is not None:
        await assign(db, user, role)
    raw, _, (account,) = await make_device(db, user)
    return user, raw, account


async def _count(db: AsyncSession) -> tuple[int, int]:
    reports = await db.scalar(select(func.count()).select_from(BugReport))
    audits = await db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "bug_report.created")
    )
    return int(reports or 0), int(audits or 0)


async def _seed(
    db: AsyncSession,
    user: User,
    app: App,
    n: int,
    *,
    hours_ago: float = 0,
    status: str = "open",
) -> None:
    for _ in range(n):
        report = BugReport(app_id=app.id, user_id=user.id, description="old", status=status)
        if status == "done":
            report.handled_at = datetime.now(UTC)
        db.add(report)
    await db.flush()
    if hours_ago:
        await db.execute(
            text("UPDATE bug_reports SET created_at = now() - make_interval(hours => :h)"),
            {"h": hours_ago},
        )


# --- happy path ----------------------------------------------------------------


async def test_submit_201_records_reporter_and_app(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    user, raw, account = await _member(db_session, role)
    resp = await client.post(
        URL,
        json=_body(
            account,
            page_url="https://Example.com/x?token=s#f",
            app_version="1.2.3",
            user_agent="UA/1.0",
        ),
        headers=_headers(raw),
    )
    assert resp.status_code == 201
    assert resp.headers["cache-control"] == "no-store"
    assert set(resp.json()) == {"id", "status", "created_at"}
    assert resp.json()["status"] == "open"
    row = await db_session.get(BugReport, uuid.UUID(resp.json()["id"]))
    assert row is not None
    assert (row.user_id, row.app_id, row.status) == (user.id, app.id, "open")
    assert (row.handled_by, row.handled_at, row.org_id) == (None, None, None)
    assert (row.page_url, row.app_version, row.user_agent) == (
        "https://example.com/x",
        "1.2.3",
        "UA/1.0",
    )


async def test_submit_rotated_cookie_is_set(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    device = await db_session.scalar(select(AccountSession.device_session_id))
    await shift(db_session, "device_sessions", "rotated_at", device, "-25 hours")
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 201
    assert parse_set_cookie(resp) is not None


async def test_submit_with_valid_org_id_201(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _app(db_session, org_mode="required")
    org = await make_org(db_session)
    user, raw, account = await _member(db_session, role)
    org_role = await make_role(db_session, (await db_session.get(App, role.app_id)), "ricotdin:org")  # type: ignore[arg-type]
    await assign(db_session, user, org_role, org_id=org.id)
    resp = await client.post(URL, json=_body(account, org_id=str(org.id)), headers=_headers(raw))
    assert resp.status_code == 201
    row = await db_session.get(BugReport, uuid.UUID(resp.json()["id"]))
    assert row is not None and row.org_id == org.id


@pytest.mark.parametrize("field", ["app_key", "app", "user_id", "status"])
async def test_submit_body_extra_field_rejected_422(
    client: AsyncClient, db_session: AsyncSession, field: str
) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(account, **{field: "x"}), headers=_headers(raw))
    assert resp.status_code == 422
    assert await _count(db_session) == (0, 0)


# --- org_id ----------------------------------------------------------------------


async def test_submit_org_id_not_member_403(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _app(db_session, org_mode="required")
    org = await make_org(db_session)
    _, raw, account = await _member(db_session, role)
    known = await client.post(URL, json=_body(account, org_id=str(org.id)), headers=_headers(raw))
    unknown = await client.post(
        URL, json=_body(account, org_id=str(uuid.uuid4())), headers=_headers(raw)
    )
    assert known.status_code == unknown.status_code == 403
    assert _error(known) == _error(unknown)
    assert _error(known)["code"] == "org_access_denied"
    assert await _count(db_session) == (0, 0)


async def test_submit_org_id_on_org_less_app_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    resp = await client.post(
        URL, json=_body(account, org_id=str(uuid.uuid4())), headers=_headers(raw)
    )
    assert resp.status_code == 422
    assert _error(resp)["code"] == "org_not_supported"


# --- refusals --------------------------------------------------------------------


async def test_submit_no_cookie_401(client: AsyncClient, db_session: AsyncSession) -> None:
    await _app(db_session)
    resp = await client.post(
        URL,
        json={"account_id": str(uuid.uuid4()), "description": "x"},
        headers=auth_headers(app_key="ricotdin", origin=ORIGIN),
    )
    assert resp.status_code == 401
    assert await _count(db_session) == (0, 0)


async def test_submit_invalid_cookie_401(client: AsyncClient, db_session: AsyncSession) -> None:
    await _app(db_session)
    resp = await client.post(
        URL, json={"account_id": str(uuid.uuid4()), "description": "x"}, headers=_headers("nope")
    )
    assert resp.status_code == 401
    assert await _count(db_session) == (0, 0)


async def test_submit_foreign_account_id_401(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _app(db_session)
    _, raw, _ = await _member(db_session, role)
    _, _, other_account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(other_account), headers=_headers(raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert await _count(db_session) == (0, 0)


async def test_submit_disabled_user_401_session_invalid(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role, status="disabled")
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 401
    assert _error(resp)["code"] == "session_invalid"
    assert await _count(db_session) == (0, 0)


async def test_submit_missing_app_header_403(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    resp = await client.post(
        URL, json=_body(account), headers=auth_headers(app_key=None, cookie=raw)
    )
    assert resp.status_code == 403
    assert await _count(db_session) == (0, 0)


async def test_submit_origin_of_other_app_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _app(db_session)
    await make_login_app(db_session, "calechip", origins=[OTHER_ORIGIN])
    _, raw, account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(account), headers=_headers(raw, origin=OTHER_ORIGIN))
    assert resp.status_code == 403
    assert await _count(db_session) == (0, 0)


async def test_submit_missing_origin_403(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    resp = await client.post(
        URL, json=_body(account), headers=auth_headers(app_key="ricotdin", origin=None, cookie=raw)
    )
    assert resp.status_code == 403


async def test_submit_unknown_app_403(client: AsyncClient, db_session: AsyncSession) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(account), headers=_headers(raw, app_key="nope"))
    assert resp.status_code == 403


async def test_submit_disabled_app_403(client: AsyncClient, db_session: AsyncSession) -> None:
    app, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    app.status = "disabled"
    await db_session.flush()
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert await _count(db_session) == (0, 0)


async def test_submit_admin_app_403(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await db_session.scalar(select(App).where(App.key == "admin"))
    assert admin is not None
    admin.origins = ["https://admin.mmlabs.online"]
    user = await make_login_user(db_session)
    raw, _, (account,) = await make_device(db_session, user)
    resp = await client.post(
        URL,
        json=_body(account),
        headers=_headers(raw, app_key="admin", origin="https://admin.mmlabs.online"),
    )
    assert resp.status_code == 403
    assert _error(resp)["code"] == "origin_not_allowed"
    assert parse_set_cookie(resp) is None
    assert await _count(db_session) == (0, 0)


async def test_submit_unverified_403_email_unverified(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role, verified=False)
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "email_unverified"
    assert await _count(db_session) == (0, 0)


async def test_submit_non_member_403_not_member(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _app(db_session, join_policy="open")
    _, raw, account = await _member(db_session, None)
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 403
    assert _error(resp)["code"] == "not_member"
    assert "join_available" in _error(resp)["details"]
    assert await _count(db_session) == (0, 0)


# --- validation ------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"description": ""},
        {"description": "   "},
        {"description": "x" * 5001},
        {"description": "a\x00b"},
        {"description": None},
        {"page_url": "javascript:alert(1)"},
        {"page_url": "https://h/" + "a" * 2048},
        {"page_url": ""},
        {"app_version": "v" * 65},
        {"app_version": ""},
        {"user_agent": "u" * 513},
        {"user_agent": "a\x07"},
        {"org_id": "not-a-uuid"},
    ],
    ids=lambda e: next(iter(e)) + "-" + str(len(str(next(iter(e.values()))))),
)
async def test_submit_validation_422(
    client: AsyncClient, db_session: AsyncSession, extra: dict[str, Any]
) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    resp = await client.post(URL, json={**_body(account), **extra}, headers=_headers(raw))
    assert resp.status_code == 422
    assert _error(resp)["code"] == "validation_error"
    assert await _count(db_session) == (0, 0)


async def test_submit_page_url_stored_reduced(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    resp = await client.post(
        URL,
        json=_body(account, page_url="https://u:p@Example.COM:8443/a/b?q=1#token=x"),
        headers=_headers(raw),
    )
    assert resp.status_code == 201
    row = await db_session.get(BugReport, uuid.UUID(resp.json()["id"]))
    assert row is not None and row.page_url == "https://example.com:8443/a/b"


# --- rate limits and caps ----------------------------------------------------------


async def test_submit_per_ip_rate_limited_429(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    async with make_client(bug_report_rate_limit=1, **NO_BURST) as client:
        first = await client.post(URL, json=_body(account), headers=_headers(raw))
        second = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert first.status_code == 201
    assert second.status_code == 429
    assert _error(second)["code"] == "rate_limited"
    assert "retry-after" in second.headers
    assert await _count(db_session) == (1, 1)


async def test_submit_per_user_burst_429(make_client: MakeClient, db_session: AsyncSession) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    async with make_client(bug_report_user_rate_limit=1) as client:
        await client.post(URL, json=_body(account), headers=_headers(raw))
        second = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert second.status_code == 429
    assert _error(second)["code"] == "rate_limited"
    assert await _count(db_session) == (1, 1)


async def test_submit_daily_cap_429_retry_after(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    other, _ = await _app(db_session, "calechip", origins=[OTHER_ORIGIN])
    user, raw, account = await _member(db_session, role)
    await _seed(db_session, user, app, 5, hours_ago=2)
    await _seed(db_session, user, other, 5, status="done")
    await db_session.execute(
        text("UPDATE bug_reports SET created_at = now() - interval '1 hour' WHERE status = 'done'")
    )
    async with make_client(bug_report_max_open_per_user_app=50, **NO_BURST) as client:
        resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 429
    assert _error(resp)["code"] == "bug_report_daily_limit"
    wait = int(resp.headers["retry-after"])
    assert abs(wait - 22 * 3600) <= 5
    assert _error(resp)["details"]["retry_after"] == wait
    assert (await _count(db_session))[1] == 0


async def test_submit_old_reports_do_not_count(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    user, raw, account = await _member(db_session, role)
    await _seed(db_session, user, app, 10, hours_ago=25, status="done")
    async with make_client(**NO_BURST) as client:
        resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 201


async def test_submit_open_cap_429_per_app(
    make_client: MakeClient, db_session: AsyncSession
) -> None:
    app, role = await _app(db_session)
    _other, other_role = await _app(db_session, "calechip", origins=[OTHER_ORIGIN])
    user, raw, account = await _member(db_session, role)
    await assign(db_session, user, other_role)
    await _seed(db_session, user, app, 3)
    async with make_client(bug_report_max_open_per_user_app=3, **NO_BURST) as client:
        blocked = await client.post(URL, json=_body(account), headers=_headers(raw))
        elsewhere = await client.post(
            URL, json=_body(account), headers=_headers(raw, "calechip", OTHER_ORIGIN)
        )
        await db_session.execute(
            text(
                "UPDATE bug_reports SET status = 'done', handled_at = now() "
                "WHERE id = (SELECT id FROM bug_reports WHERE app_id = :a LIMIT 1)"
            ),
            {"a": app.id},
        )
        freed = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert blocked.status_code == 429
    assert _error(blocked)["code"] == "bug_report_open_limit"
    assert _error(blocked)["details"] == {"limit": 3}
    assert "retry-after" not in blocked.headers
    assert elsewhere.status_code == 201
    assert freed.status_code == 201


# --- audit and logs ------------------------------------------------------------------


async def test_submit_audit_row_ids_only(client: AsyncClient, db_session: AsyncSession) -> None:
    app, role = await _app(db_session)
    user, raw, account = await _member(db_session, role)
    resp = await client.post(
        URL,
        json=_body(account, page_url="https://h.example/secret-path", app_version="9.9.9"),
        headers=_headers(raw),
    )
    assert resp.status_code == 201
    row = await db_session.scalar(select(AuditLog).where(AuditLog.action == "bug_report.created"))
    assert row is not None
    assert (row.target_type, row.target_id) == ("bug_report", uuid.UUID(resp.json()["id"]))
    assert (row.app_key, row.actor_user_id, row.actor_kind, row.org_id) == (
        app.key,
        user.id,
        "user",
        None,
    )
    assert row.outcome == "success"
    assert set(row.changes or {}) == {"attachment_count", "release_id", "release_match"}
    dumped = str((row.changes, row.user_agent))
    for secret in ("save button", "secret-path", "9.9.9"):
        assert secret not in dumped


async def test_submit_logs_no_content(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Alembic's `fileConfig` (test DB setup) disables existing loggers.
    monkeypatch.setattr(service.logger, "disabled", False)
    _, role = await _app(db_session)
    user, raw, account = await _member(db_session, role)
    with caplog.at_level(logging.INFO, logger="app.modules.bug_reports.service"):
        resp = await client.post(
            URL,
            json=_body(
                account,
                description="unique-description-text",
                page_url="https://h.example/unique-path",
                app_version="unique-ver",
                user_agent="unique-agent",
            ),
            headers=_headers(raw),
        )
    assert resp.status_code == 201
    assert "bug_report_created" in caplog.text
    for secret in (
        "unique-description-text",
        "unique-path",
        "unique-ver",
        "unique-agent",
        user.email,
    ):
        assert secret not in caplog.text


async def test_submit_audit_failure_rolls_back_report(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    await db_session.commit()

    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(AuditLogger, "record", boom)
    async with make_strict_client() as client:
        with pytest.raises(RuntimeError):
            await client.post(URL, json=_body(account), headers=_headers(raw))
    assert await _count(db_session) == (0, 0)


# --- CORS ----------------------------------------------------------------------------


def _preflight(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-mm-app",
    }


async def test_bug_reports_preflight_registered_origin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session, "ricotdin")
    resp = await client.options(URL, headers=_preflight(ORIGIN))
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert "X-MM-App" in resp.headers["access-control-allow-headers"]


async def test_bug_reports_preflight_unregistered_origin_no_acao(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_app(db_session, "ricotdin")
    resp = await client.options(URL, headers=_preflight("https://evil.mmlabs.online"))
    assert "access-control-allow-origin" not in resp.headers


async def test_bug_reports_post_response_has_acao(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, role = await _app(db_session)
    _, raw, account = await _member(db_session, role)
    resp = await client.post(URL, json=_body(account), headers=_headers(raw))
    assert resp.status_code == 201
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
