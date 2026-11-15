"""Identity audit events (ADM-004b): one row per covered event, none for refusals, and an audit
write failure on a failure path never changes the response.

Harness: setup is committed before each request (`record_failure` rolls back, which under
`create_savepoint` would erase uncommitted setup) and ids are copied first (the rollback expires
ORM objects). Rows are read back through `db_session`.
"""

import logging
import secrets
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import service as audit_service
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditRepository
from app.modules.auth.email_verification import VERIFY, VERIFY_TTL
from app.modules.auth.password_reset import RESET, RESET_TTL
from app.modules.auth.repository import EmailTokenRepository, LoginFailureRepository
from app.modules.auth.throttle import EmailHasher
from app.modules.sessions.models import AccountSession, DeviceSession
from tests.auth_factories import (
    HMAC_KEY,
    LOGIN,
    LOGOUT,
    PASSWORD,
    auth_headers,
    make_device,
    make_login_app,
    make_login_user,
    parse_set_cookie,
    sha256,
)
from tests.conftest import MakeClient, MakeStrictApp

WRONG = "wrong password entirely"
VERIFY_URL = "/api/v1/auth/verify-email"
RESET_URL = "/api/v1/auth/password/reset"
NEW_PASSWORD = "a brand new passphrase 42"


@pytest.fixture(autouse=True)
async def _app(db_session: AsyncSession) -> None:
    await make_login_app(db_session)


@pytest.fixture
def audit_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> Any:
    """Alembic's `fileConfig` (session fixture) disables loggers created before it ran."""
    monkeypatch.setattr(audit_service.logger, "disabled", False)
    caplog.set_level(logging.INFO)
    return caplog


@pytest.fixture
def failing_add(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(self: AuditRepository, row: AuditLog) -> None:
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(AuditRepository, "add", boom)


async def _rows(db_session: AsyncSession, action: str | None = None) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.occurred_at, AuditLog.id)
    if action is not None:
        query = query.where(AuditLog.action == action)
    return list(await db_session.scalars(query.execution_options(populate_existing=True)))


async def _identity_rows(db_session: AsyncSession) -> list[AuditLog]:
    return [r for r in await _rows(db_session) if r.action.startswith(("auth.", "device_session."))]


async def _login(client: AsyncClient, email: str, password: str = PASSWORD, **kw: Any) -> Response:
    cookie = kw.pop("cookie", None)
    return await client.post(
        LOGIN,
        json={"email": email, "password": password, **kw},
        headers=auth_headers(cookie=cookie),
    )


async def _with_prev(db_session: AsyncSession, device_id: uuid.UUID, delta: str) -> None:
    """Make the device's cookie the previous value; its grace ends at now() + delta."""
    await db_session.execute(
        text(
            "UPDATE device_sessions SET prev_token_hash = token_hash, token_hash = :new, "
            "prev_valid_until = now() + CAST(CAST(:d AS text) AS interval) WHERE id = :id"
        ),
        {"new": sha256("current-" + uuid.uuid4().hex), "d": delta, "id": device_id},
    )


async def _set_failures(db_session: AsyncSession, email: str, failures: int) -> None:
    await db_session.execute(
        text(
            "INSERT INTO login_failures (email_hmac, failures, last_failed_at) "
            "VALUES (:h, :f, now())"
        ),
        {"h": EmailHasher(HMAC_KEY).hmac(email), "f": failures},
    )


async def _failures(db_session: AsyncSession, email: str) -> int | None:
    value: int | None = await db_session.scalar(
        text("SELECT failures FROM login_failures WHERE email_hmac = :h"),
        {"h": EmailHasher(HMAC_KEY).hmac(email)},
    )
    return value


async def _token(db_session: AsyncSession, user_id: uuid.UUID, purpose: str) -> str:
    raw = secrets.token_urlsafe(32)
    ttl = RESET_TTL if purpose == RESET else VERIFY_TTL
    await EmailTokenRepository(db_session).issue(user_id, purpose, sha256(raw), ttl)
    return raw


# --- login -------------------------------------------------------------------------


async def test_login_success_row(make_strict_client: MakeClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    email, user_id = user.email, user.id
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await _login(client, email)
    assert resp.status_code == 200, resp.text
    (row,) = await _rows(db_session, "auth.login_succeeded")
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("user", user_id, None)
    assert (row.target_type, row.target_id, row.app_key) == ("user", user_id, "stoktok")
    assert row.outcome == "success"
    assert row.changes is not None
    assert set(row.changes) == {"account_session_id", "device_session_id"}
    assert row.changes["account_session_id"] == resp.json()["account_id"]
    cookie = parse_set_cookie(resp)
    assert cookie is not None
    device_id = await db_session.scalar(
        select(DeviceSession.id).where(DeviceSession.token_hash == sha256(cookie.value))
    )
    assert row.changes["device_session_id"] == str(device_id)
    assert str(row.ip) == "127.0.0.1" and row.request_id


async def test_login_failed_unknown_email_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await _login(client, "nobody@example.com", WRONG)
    assert resp.status_code == 401
    (row,) = await _identity_rows(db_session)
    assert row.action == "auth.login_failed"
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("anonymous", None, None)
    assert (row.target_type, row.target_id, row.app_key) == (None, None, "stoktok")
    assert (row.changes, row.outcome) == ({"reason": "unknown_account"}, "failure")


async def test_login_failed_wrong_password_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    email, user_id = user.email, user.id
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await _login(client, email, WRONG)
    assert resp.status_code == 401
    (row,) = await _identity_rows(db_session)
    assert row.action == "auth.login_failed"
    assert (row.actor_kind, row.actor_user_id) == ("anonymous", None)
    assert (row.target_type, row.target_id) == ("user", user_id)
    assert (row.changes, row.outcome) == (
        {"reason": "invalid_credentials", "failures": 1},
        "failure",
    )


async def test_login_failed_disabled_user_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, status="disabled")
    email, user_id = user.email, user.id
    await db_session.commit()
    async with make_strict_client() as client:
        right = await _login(client, email)
        wrong = await _login(client, email, WRONG)
    assert right.status_code == wrong.status_code == 401
    assert right.json() == wrong.json()
    # One test transaction: both rows share `occurred_at`, so order by the failure count.
    rows = sorted(await _identity_rows(db_session), key=lambda r: (r.changes or {})["failures"])
    assert [(r.target_type, r.target_id) for r in rows] == [("user", user_id)] * 2
    assert [r.changes for r in rows] == [
        {"reason": "user_disabled", "failures": 1},
        {"reason": "invalid_credentials", "failures": 2},
    ]


@pytest.mark.parametrize(
    "refusal", ["blocked", "turnstile_required", "turnstile_failed", "reserve_none"]
)
async def test_login_refusals_write_no_row(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    refusal: str,
) -> None:
    user = await make_login_user(db_session)
    email = user.email
    extra: dict[str, Any] = {}
    if refusal == "blocked":
        await _set_failures(db_session, email, 6)
        await db_session.execute(
            text("UPDATE login_failures SET blocked_until = now() + interval '5 minutes'")
        )
    elif refusal == "turnstile_required":
        await _set_failures(db_session, email, 3)
    elif refusal == "turnstile_failed":
        await _set_failures(db_session, email, 3)
        extra = {"turnstile_token": "nope"}
    else:

        async def no_reserve(self: LoginFailureRepository, *a: Any, **k: Any) -> None:
            return None

        monkeypatch.setattr(LoginFailureRepository, "reserve", no_reserve)
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await _login(client, email, **extra)
    assert resp.status_code in {403, 429}, resp.text
    assert await _identity_rows(db_session) == []


# --- logout ------------------------------------------------------------------------


async def test_logout_one_account_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, _, (account,) = await make_device(db_session, user)
    user_id, account_id = user.id, account.id
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(
            LOGOUT, json={"account_id": str(account_id)}, headers=auth_headers(cookie=raw)
        )
    assert resp.status_code == 204, resp.text
    (row,) = await _identity_rows(db_session)
    assert row.action == "auth.logged_out"
    assert (row.actor_kind, row.actor_user_id) == ("user", user_id)
    assert (row.target_type, row.target_id, row.app_key) == ("user", user_id, "stoktok")
    assert row.changes == {"account_session_id": str(account_id)}


async def test_logout_unknown_account_no_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, _, _ = await make_device(db_session, user)
    foreign = await make_login_user(db_session)
    _, _, (foreign_account,) = await make_device(db_session, foreign)
    ids = [uuid.uuid4(), foreign_account.id]
    await db_session.commit()
    async with make_strict_client() as client:
        for account_id in ids:
            resp = await client.post(
                LOGOUT, json={"account_id": str(account_id)}, headers=auth_headers(cookie=raw)
            )
            assert resp.status_code == 204, resp.text
    assert await _identity_rows(db_session) == []


async def test_logout_all_row(make_strict_client: MakeClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    other = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user, other)
    device_id = device.id
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(LOGOUT, json={"all": True}, headers=auth_headers(cookie=raw))
    assert resp.status_code == 204, resp.text
    (row,) = await _identity_rows(db_session)
    assert row.action == "auth.logged_out_all"
    assert (row.actor_kind, row.actor_user_id) == ("anonymous", None)
    assert (row.target_type, row.target_id) == ("device_session", device_id)
    assert row.changes == {"accounts_revoked": 2}


# --- device-session reuse ----------------------------------------------------------


async def test_reuse_on_cookie_route_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    device_id = device.id
    await _with_prev(db_session, device_id, "-1 second")
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(LOGOUT, json={"all": True}, headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"
    (row,) = await _identity_rows(db_session)  # committed despite the 401
    assert row.action == "device_session.reuse_revoked"
    assert (row.actor_kind, row.target_type, row.target_id) == (
        "anonymous",
        "device_session",
        device_id,
    )
    assert (row.app_key, row.outcome) == (None, "success")
    assert row.changes == {"accounts_revoked": 1, "during_login": False}
    assert str(row.ip) == "127.0.0.1"


async def test_reuse_during_login_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    email, device_id = user.email, device.id
    await _with_prev(db_session, device_id, "-1 second")
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await _login(client, email, cookie=raw)
    assert resp.status_code == 200, resp.text
    (row,) = await _rows(db_session, "device_session.reuse_revoked")
    assert (row.target_type, row.target_id) == ("device_session", device_id)
    assert row.changes == {"accounts_revoked": 1, "during_login": True}
    assert len(await _rows(db_session, "auth.login_succeeded")) == 1


async def test_reuse_already_revoked_no_second_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    await _with_prev(db_session, device.id, "-1 second")
    await db_session.commit()
    async with make_strict_client() as client:
        for _ in range(2):
            resp = await client.post(LOGOUT, json={"all": True}, headers=auth_headers(cookie=raw))
            assert resp.status_code == 401
    assert len(await _identity_rows(db_session)) == 1


async def test_reuse_during_login_audit_failure_is_500(
    make_strict_app: MakeStrictApp, db_session: AsyncSession, failing_add: None
) -> None:
    """Inside login the reuse row is part of the success transaction (plan A5)."""
    user = await make_login_user(db_session)
    raw, device, _ = await make_device(db_session, user)
    email = user.email
    await _with_prev(db_session, device.id, "-1 second")
    await db_session.commit()
    transport = ASGITransport(app=make_strict_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await _login(client, email, cookie=raw)
    assert resp.status_code == 500


# --- password reset and email verification -------------------------------------------


async def test_password_reset_row(make_strict_client: MakeClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session, verified=False)
    await make_device(db_session, user)
    user_id = user.id
    token = await _token(db_session, user_id, RESET)
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(RESET_URL, json={"token": token, "new_password": NEW_PASSWORD})
    assert resp.status_code == 204, resp.text
    (row,) = await _identity_rows(db_session)
    assert row.action == "auth.password_reset_completed"
    assert (row.actor_kind, row.actor_user_id) == ("user", user_id)
    assert (row.target_type, row.target_id, row.app_key) == ("user", user_id, None)
    assert row.changes == {"account_sessions_revoked": 1, "verified": True}


async def test_verify_email_row(make_strict_client: MakeClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session, verified=False)
    user_id = user.id
    token = await _token(db_session, user_id, VERIFY)
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(VERIFY_URL, json={"token": token})
    assert resp.status_code == 204, resp.text
    (row,) = await _identity_rows(db_session)
    assert row.action == "auth.email_verified"
    assert (row.actor_kind, row.actor_user_id) == ("user", user_id)
    assert (row.target_type, row.target_id, row.changes) == ("user", user_id, None)


async def test_verify_email_idempotent_no_row(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, verified=False)
    token = await _token(db_session, user.id, VERIFY)
    await db_session.commit()
    async with make_strict_client() as client:
        for _ in range(2):
            resp = await client.post(VERIFY_URL, json={"token": token})
            assert resp.status_code == 204, resp.text
    assert len(await _identity_rows(db_session)) == 1


async def test_bad_token_no_row(make_strict_client: MakeClient, db_session: AsyncSession) -> None:
    await db_session.commit()
    async with make_strict_client() as client:
        verify = await client.post(VERIFY_URL, json={"token": "x" * 43})
        reset = await client.post(RESET_URL, json={"token": "x" * 43, "new_password": NEW_PASSWORD})
    assert verify.status_code == reset.status_code == 422
    assert verify.json()["error"]["code"] == reset.json()["error"]["code"] == "token_invalid"
    assert await _identity_rows(db_session) == []


# --- audit write failures on failure paths --------------------------------------------


async def test_audit_add_fails_login_still_401(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    audit_logs: pytest.LogCaptureFixture,
) -> None:
    baseline_user = await make_login_user(db_session)
    user = await make_login_user(db_session)
    baseline_email, email = baseline_user.email, user.email
    await db_session.commit()
    async with make_strict_client() as client:
        expected = await _login(client, baseline_email, WRONG)

        async def boom(self: AuditRepository, row: AuditLog) -> None:
            raise RuntimeError("audit insert failed")

        monkeypatch.setattr(AuditRepository, "add", boom)
        resp = await _login(client, email, WRONG)
    assert (resp.status_code, resp.json()) == (expected.status_code, expected.json())
    assert resp.status_code == 401
    assert await _failures(db_session, email) == 1  # the reservation survived
    assert "audit_write_failed action=auth.login_failed outcome=failure" in audit_logs.text
    assert email not in audit_logs.text


async def test_audit_add_fails_reuse_still_401_and_revoked(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    failing_add: None,
    audit_logs: pytest.LogCaptureFixture,
) -> None:
    user = await make_login_user(db_session)
    raw, device, (account,) = await make_device(db_session, user)
    device_id, account_id = device.id, account.id
    await _with_prev(db_session, device_id, "-1 second")
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await client.post(LOGOUT, json={"all": True}, headers=auth_headers(cookie=raw))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"
    device_revoked = await db_session.scalar(
        select(DeviceSession.revoked_at).where(DeviceSession.id == device_id)
    )
    account_revoked = await db_session.scalar(
        select(AccountSession.revoked_at).where(AccountSession.id == account_id)
    )
    assert device_revoked is not None and account_revoked is not None
    assert "audit_write_failed action=device_session.reuse_revoked" in audit_logs.text


# --- no secrets ----------------------------------------------------------------------


async def test_no_secret_in_identity_rows(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, verified=False)
    email, user_id = user.email, user.id
    unknown = "ghost@example.com"
    reuse_raw, reuse_device, _ = await make_device(db_session, user)
    await _with_prev(db_session, reuse_device.id, "-1 second")
    verify_token = await _token(db_session, user_id, VERIFY)
    await db_session.commit()
    async with make_strict_client() as client:
        assert (await _login(client, unknown, WRONG)).status_code == 401
        assert (await _login(client, email, WRONG)).status_code == 401
        ok = await _login(client, email)
        assert ok.status_code == 200
        cookie = parse_set_cookie(ok)
        assert cookie is not None
        resp = await client.post(
            LOGOUT,
            json={"account_id": ok.json()["account_id"]},
            headers=auth_headers(cookie=cookie.value),
        )
        assert resp.status_code == 204
        resp = await client.post(LOGOUT, json={"all": True}, headers=auth_headers(cookie=reuse_raw))
        assert resp.status_code == 401
        assert (await client.post(VERIFY_URL, json={"token": verify_token})).status_code == 204
        reset_token = await _token(db_session, user_id, RESET)
        await db_session.commit()
        resp = await client.post(
            RESET_URL, json={"token": reset_token, "new_password": NEW_PASSWORD}
        )
        assert resp.status_code == 204
    rows = await _identity_rows(db_session)
    assert {r.action for r in rows} == {
        "auth.login_failed",
        "auth.login_succeeded",
        "auth.logged_out",
        "device_session.reuse_revoked",
        "auth.email_verified",
        "auth.password_reset_completed",
    }
    needles = [
        email,
        unknown,
        "@",
        PASSWORD,
        WRONG,
        NEW_PASSWORD,
        cookie.value,
        reuse_raw,
        verify_token,
        reset_token,
        EmailHasher(HMAC_KEY).hmac(email).hex(),
        EmailHasher(HMAC_KEY).hmac(unknown).hex(),
    ]
    for row in rows:
        haystack = f"{row.changes} {row.actor_label} {row.user_agent} {row.request_id}"
        for needle in needles:
            assert needle not in haystack, (row.action, needle)
