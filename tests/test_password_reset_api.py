"""USR-04b: POST /auth/password/forgot and /auth/password/reset (fake sender, no SMTP)."""

import logging
import re
import secrets
import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.main import create_app
from app.modules.auth import hooks as hooks_module
from app.modules.auth import password_reset as reset_module
from app.modules.auth.deps import get_email_dispatcher, get_link_origin, get_turnstile_verifier
from app.modules.auth.models import EmailSendLog, EmailToken, LoginFailure
from app.modules.auth.password_reset import RESET_TTL
from app.modules.auth.repository import EmailTokenRepository
from app.modules.auth.throttle import EmailHasher
from app.modules.email.dispatcher import EmailDispatcher
from app.modules.email.sender import OutgoingEmail
from app.modules.rbac.models import App
from app.modules.sessions.models import DeviceSession
from app.modules.users.models import User
from tests.auth_factories import (
    HMAC_KEY,
    LOGIN,
    ORIGIN,
    PASSWORD,
    auth_headers,
    login_body,
    make_device,
    make_login_app,
    make_login_user,
    sha256,
)
from tests.conftest import MakeClient
from tests.fakes import FakeEmailSender, FakeTurnstileVerifier
from tests.rbac_factories import assign, make_role

FORGOT = "/api/v1/auth/password/forgot"
RESET = "/api/v1/auth/password/reset"
SIGNUP = "/api/v1/auth/signup"
TOKEN = "/api/v1/auth/token"
NEW_PASSWORD = "a brand new passphrase 42"
LINK_RE = re.compile(r"(https://\S+)/reset-password#token=([A-Za-z0-9_-]+)")
TOKEN_INVALID = {
    "error": {"code": "token_invalid", "message": "Token is invalid or expired", "details": None}
}


@pytest.fixture(autouse=True)
async def stoktok(db_session: AsyncSession) -> App:
    """Forgot requires app context (owner 2026-11-13), so every test here has `stoktok`."""
    return await make_login_app(db_session)


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> Any:
    """Alembic's `fileConfig` (session fixture) disables loggers created before it ran."""
    monkeypatch.setattr(reset_module.logger, "disabled", False)
    monkeypatch.setattr(hooks_module.logger, "disabled", False)
    caplog.set_level(logging.INFO)
    return caplog


async def _forgot(
    client: AsyncClient,
    dispatcher: EmailDispatcher,
    email: str,
    headers: dict[str, str] | None = None,
    turnstile_token: str = "pass",  # noqa: S107 - the fake verifier's token
) -> Response:
    headers = auth_headers() if headers is None else headers
    resp = await client.post(
        FORGOT, json={"email": email, "turnstile_token": turnstile_token}, headers=headers
    )
    await dispatcher.join()
    return resp


def _link(email: OutgoingEmail) -> tuple[str, str]:
    match = LINK_RE.search(email.body)
    assert match is not None
    return match.group(1), match.group(2)


async def _reset(client: AsyncClient, token: str, password: str = NEW_PASSWORD) -> Response:
    return await client.post(RESET, json={"token": token, "new_password": password})


async def _issue(
    db_session: AsyncSession, user: User, purpose: str = "reset", ttl: timedelta = RESET_TTL
) -> str:
    """Issue a token the way the service does (supersedes older ones); the raw value."""
    raw = secrets.token_urlsafe(32)
    await EmailTokenRepository(db_session).issue(user.id, purpose, sha256(raw), ttl)
    return raw


async def _count(db_session: AsyncSession, model: Any) -> int:
    return await db_session.scalar(select(func.count()).select_from(model)) or 0


async def _age_send_log(db_session: AsyncSession, delta: str = "-2 minutes") -> None:
    await db_session.execute(
        text("UPDATE email_send_log SET sent_at = now() + CAST(CAST(:d AS text) AS interval)"),
        {"d": delta},
    )


async def _used_at(db_session: AsyncSession, raw: str) -> Any:
    return await db_session.scalar(
        select(EmailToken.used_at)
        .where(EmailToken.token_hash == sha256(raw))
        .execution_options(populate_existing=True)
    )


async def _user_row(db_session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db_session.scalar(
        select(User).where(User.id == user_id).execution_options(populate_existing=True)
    )
    assert user is not None
    return user


# --- POST /auth/password/forgot -------------------------------------------------------------


async def test_forgot_known_user_202_sends_reset_link(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await make_login_user(db_session, email="dana@example.com")
    resp = await _forgot(client, email_dispatcher, "  Dana@Example.com ")
    assert resp.status_code == 202, resp.text
    assert resp.content == b""
    assert resp.headers["cache-control"] == "no-store"
    (email,) = fake_email.sent
    assert email.to == "dana@example.com"
    assert email.purpose == "reset"
    assert email.user_id == user.id
    origin, token = _link(email)
    assert origin == ORIGIN
    assert len(token) == 43
    row = (await db_session.execute(select(EmailToken))).scalar_one()
    assert row.purpose == "reset"
    assert row.user_id == user.id
    assert row.token_hash == sha256(token)
    lifetime = await db_session.scalar(
        select(EmailToken.expires_at - EmailToken.created_at).where(EmailToken.id == row.id)
    )
    assert lifetime == timedelta(minutes=30)
    rows = (await db_session.execute(select(EmailSendLog.purpose, EmailSendLog.email_hmac))).all()
    assert [(r.purpose, r.email_hmac) for r in rows] == [
        ("reset", EmailHasher(HMAC_KEY).hmac("dana@example.com"))
    ]


async def test_forgot_link_uses_request_origin(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    other = "https://calechip.mmlabs.online"
    await make_login_app(db_session, "calechip", origins=[other])
    user = await make_login_user(db_session)
    resp = await _forgot(client, email_dispatcher, user.email, auth_headers("calechip", other))
    assert resp.status_code == 202
    (email,) = fake_email.sent
    assert _link(email)[0] == other


async def _assert_no_mail_same_write(
    resp: Response, db_session: AsyncSession, fake_email: FakeEmailSender
) -> None:
    assert resp.status_code == 202
    assert resp.content == b""
    assert resp.headers["cache-control"] == "no-store"
    assert fake_email.calls == 0
    assert await _count(db_session, EmailToken) == 0
    purposes = (await db_session.scalars(select(EmailSendLog.purpose))).all()
    assert purposes == ["reset"]  # the same write as for a known email


async def test_forgot_unknown_email_202_no_mail(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    resp = await _forgot(client, email_dispatcher, "nobody@example.com")
    await _assert_no_mail_same_write(resp, db_session, fake_email)


async def test_forgot_disabled_user_202_no_mail(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await make_login_user(db_session, status="disabled")
    resp = await _forgot(client, email_dispatcher, user.email)
    await _assert_no_mail_same_write(resp, db_session, fake_email)


async def test_forgot_per_email_limit_silent_202(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await make_login_user(db_session)
    assert (await _forgot(client, email_dispatcher, user.email)).status_code == 202
    second = await _forgot(client, email_dispatcher, user.email)
    assert second.status_code == 202
    assert second.content == b""
    assert len(fake_email.sent) == 1
    assert await _count(db_session, EmailSendLog) == 1


async def test_forgot_daily_per_email_limit_silent_202(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await make_login_user(db_session)
    email_hmac = EmailHasher(HMAC_KEY).hmac(user.email)
    for hours in range(1, 6):  # 5 sends in the last 24 h, the latest an hour ago
        await db_session.execute(
            text(
                "INSERT INTO email_send_log (id, email_hmac, purpose, sent_at) VALUES "
                "(gen_random_uuid(), :h, 'reset', now() - make_interval(hours => :n))"
            ),
            {"h": email_hmac, "n": hours},
        )
    resp = await _forgot(client, email_dispatcher, user.email)
    assert resp.status_code == 202
    assert fake_email.calls == 0
    assert await _count(db_session, EmailSendLog) == 5
    assert await _count(db_session, EmailToken) == 0


async def test_forgot_limit_applies_to_unknown_email(
    client: AsyncClient, db_session: AsyncSession, email_dispatcher: EmailDispatcher
) -> None:
    assert (await _forgot(client, email_dispatcher, "ghost@example.com")).status_code == 202
    assert (await _forgot(client, email_dispatcher, "ghost@example.com")).status_code == 202
    assert await _count(db_session, EmailSendLog) == 1


async def test_forgot_new_link_supersedes_previous(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await make_login_user(db_session)
    await _forgot(client, email_dispatcher, user.email)
    await _age_send_log(db_session)
    await _forgot(client, email_dispatcher, user.email)
    old, new = (_link(e)[1] for e in fake_email.sent)
    resp = await _reset(client, old)
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID
    assert (await _reset(client, new)).status_code == 204


async def test_forgot_turnstile_failed_403_no_writes(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await make_login_user(db_session)
    resp = await _forgot(client, email_dispatcher, user.email, turnstile_token="fail")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "turnstile_failed"
    assert await _count(db_session, EmailSendLog) == 0
    assert await _count(db_session, EmailToken) == 0
    assert fake_email.calls == 0


async def _assert_app_context_403(
    resp: Response,
    code: str,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    fake_turnstile: FakeTurnstileVerifier,
) -> None:
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == code
    assert fake_turnstile.calls == []
    assert await _count(db_session, EmailSendLog) == 0
    assert await _count(db_session, EmailToken) == 0
    assert fake_email.calls == 0


@pytest.mark.parametrize("app_key", [None, "", "Bad Key!"])
async def test_forgot_without_app_header_403_no_side_effects(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    fake_turnstile: FakeTurnstileVerifier,
    app_key: str | None,
) -> None:
    user = await make_login_user(db_session)
    resp = await _forgot(client, email_dispatcher, user.email, auth_headers(app_key=app_key))
    await _assert_app_context_403(
        resp, "app_header_required", db_session, fake_email, fake_turnstile
    )


@pytest.mark.parametrize(
    ("app_key", "origin"),
    [
        ("nope", ORIGIN),
        ("frozen", ORIGIN),
        ("stoktok", None),
        ("stoktok", "null"),
        ("stoktok", "https://other.mmlabs.online"),
    ],
)
async def test_forgot_unregistered_origin_403_no_side_effects(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    fake_turnstile: FakeTurnstileVerifier,
    app_key: str,
    origin: str | None,
) -> None:
    await make_login_app(db_session, "frozen", status="disabled")
    user = await make_login_user(db_session)
    resp = await _forgot(client, email_dispatcher, user.email, auth_headers(app_key, origin))
    await _assert_app_context_403(
        resp, "origin_not_allowed", db_session, fake_email, fake_turnstile
    )


async def test_forgot_403_when_link_origin_overridden(
    db_session: AsyncSession,
    fake_turnstile: FakeTurnstileVerifier,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    """The route-level `AppContextDep` keeps the 403 if `get_link_origin` is overridden."""
    user = await make_login_user(db_session)

    async def _session() -> Any:
        yield db_session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_turnstile_verifier] = lambda: fake_turnstile
    app.dependency_overrides[get_email_dispatcher] = lambda: email_dispatcher
    app.dependency_overrides[get_link_origin] = lambda: ORIGIN
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await _forgot(c, email_dispatcher, user.email, headers={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "app_header_required"
    assert fake_turnstile.calls == []
    assert fake_email.calls == 0
    assert await _count(db_session, EmailSendLog) == 0


async def test_forgot_per_ip_429(
    make_client: MakeClient, db_session: AsyncSession, email_dispatcher: EmailDispatcher
) -> None:
    async with make_client(forgot_rate_limit=1) as c:
        first = await _forgot(c, email_dispatcher, "a@example.com")
        second = await _forgot(c, email_dispatcher, "b@example.com")
    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(second.headers["Retry-After"]) <= 60


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "turnstile_token": "pass"},
        {"email": "a@example.com", "turnstile_token": "pass", "extra": True},
        {"email": "a@example.com"},
        {"email": "a@example.com", "turnstile_token": ""},
    ],
)
async def test_forgot_validation_422(
    client: AsyncClient, fake_email: FakeEmailSender, body: dict[str, Any]
) -> None:
    resp = await client.post(FORGOT, json=body, headers=auth_headers())
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert fake_email.calls == 0


async def test_forgot_logs_free_of_token_and_address(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    logs: pytest.LogCaptureFixture,
) -> None:
    user = await make_login_user(db_session, email="private.person@example.com")
    assert (await _forgot(client, email_dispatcher, user.email)).status_code == 202
    assert (await _forgot(client, email_dispatcher, "ghost.person@example.com")).status_code == 202
    _, token = _link(fake_email.sent[0])
    assert f"password_reset_requested user_id={user.id}" in logs.text
    assert "private.person" not in logs.text
    assert "ghost.person" not in logs.text
    assert token not in logs.text
    assert "reset-password" not in logs.text


# --- per-purpose daily cap ----------------------------------------------------------------------


async def test_unknown_email_forgot_does_not_consume_daily_cap(
    make_client: MakeClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await make_login_user(db_session)
    async with make_client(email_daily_cap=1) as c:
        for i in range(3):
            assert (await _forgot(c, email_dispatcher, f"ghost{i}@example.com")).status_code == 202
        assert (await _forgot(c, email_dispatcher, user.email)).status_code == 202
    assert [e.to for e in fake_email.sent] == [user.email]


async def test_verify_share_exhausted_reset_still_sent(
    make_client: MakeClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    logs: pytest.LogCaptureFixture,
) -> None:
    user = await make_login_user(db_session)
    async with make_client(email_daily_cap=5) as c:  # verify share: 4
        for i in range(5):
            resp = await c.post(
                SIGNUP,
                json={
                    "email": f"signup{i}@example.com",
                    "password": PASSWORD,
                    "turnstile_token": "pass",
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 201
        await email_dispatcher.join()
        assert [e.purpose for e in fake_email.sent] == ["verify"] * 4
        assert "email_not_sent reason=daily_cap purpose=verify" in logs.text
        assert (await _forgot(c, email_dispatcher, user.email)).status_code == 202
    assert [e.purpose for e in fake_email.sent] == ["verify"] * 4 + ["reset"]
    assert fake_email.sent[-1].to == user.email


async def test_forgot_daily_cap_reached_202_logged(
    make_client: MakeClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    logs: pytest.LogCaptureFixture,
) -> None:
    user = await make_login_user(db_session, email="capped.user@example.com")
    async with make_client(email_daily_cap=1) as c:
        signup = await c.post(
            SIGNUP,
            json={"email": "first@example.com", "password": PASSWORD, "turnstile_token": "pass"},
            headers=auth_headers(),
        )
        assert signup.status_code == 201
        await email_dispatcher.join()
        resp = await _forgot(c, email_dispatcher, user.email)
    assert resp.status_code == 202
    assert resp.content == b""
    assert [e.purpose for e in fake_email.sent] == ["verify"]
    assert "email_not_sent reason=daily_cap purpose=reset" in logs.text
    assert "capped.user" not in logs.text
    hmac = EmailHasher(HMAC_KEY).hmac(user.email)
    capped_rows = await db_session.scalar(
        select(func.count()).select_from(EmailSendLog).where(EmailSendLog.email_hmac == hmac)
    )
    assert capped_rows == 0  # a capped request does not use up the per-email window


# --- POST /auth/password/reset --------------------------------------------------------------


async def test_reset_happy_path_204_no_cookie_new_password_works(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await make_login_user(db_session)
    await _forgot(client, email_dispatcher, user.email)
    _, token = _link(fake_email.sent[0])
    resp = await _reset(client, token)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert resp.headers["cache-control"] == "no-store"
    assert "set-cookie" not in resp.headers
    assert await _used_at(db_session, token) is not None

    new = await client.post(LOGIN, json=login_body(user, NEW_PASSWORD), headers=auth_headers())
    assert new.status_code == 200, new.text
    old = await client.post(LOGIN, json=login_body(user, PASSWORD), headers=auth_headers())
    assert old.status_code == 401


async def test_reset_revokes_all_account_sessions_every_device(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    viewer = await make_role(db_session, stoktok, "stoktok:viewer")
    user = await make_login_user(db_session)
    other = await make_login_user(db_session)
    await assign(db_session, user, viewer)
    await assign(db_session, other, viewer)
    raw1, device1, (acc1, other_acc) = await make_device(db_session, user, other)
    raw2, device2, (acc2,) = await make_device(db_session, user)

    async def _token(raw: str, account_id: uuid.UUID) -> int:
        resp = await client.post(
            TOKEN,
            json={"audience": "stoktok", "account_id": str(account_id)},
            headers=auth_headers(cookie=raw),
        )
        return resp.status_code

    assert [await _token(raw1, acc1.id), await _token(raw2, acc2.id)] == [200, 200]
    token = await _issue(db_session, user)
    assert (await _reset(client, token)).status_code == 204

    assert await _token(raw1, acc1.id) == 401
    assert await _token(raw2, acc2.id) == 401
    assert await _token(raw1, other_acc.id) == 200
    revoked = await db_session.scalars(
        select(DeviceSession.revoked_at)
        .where(DeviceSession.id.in_([device1.id, device2.id]))
        .execution_options(populate_existing=True)
    )
    assert list(revoked) == [None, None]


@pytest.mark.parametrize(
    ("password", "reasons"),
    [("tiny pw", ["too_short"]), ("Password1234", ["too_common"])],
)
async def test_reset_weak_password_422_token_still_usable(
    client: AsyncClient, db_session: AsyncSession, password: str, reasons: list[str]
) -> None:
    user = await make_login_user(db_session)
    token = await _issue(db_session, user)
    resp = await _reset(client, token, password)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "weak_password"
    assert resp.json()["error"]["details"] == {"reasons": reasons}
    assert password not in resp.text
    assert await _used_at(db_session, token) is None
    assert (await _reset(client, token)).status_code == 204


async def test_reset_used_token_422(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    token = await _issue(db_session, user)
    assert (await _reset(client, token)).status_code == 204
    again = await _reset(client, token, "yet another passphrase 77")
    assert again.status_code == 422
    assert again.json() == TOKEN_INVALID


async def test_reset_expired_token_422(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    token = await _issue(db_session, user)
    await db_session.execute(
        text(
            "UPDATE email_tokens SET created_at = now() - interval '1 hour', "
            "expires_at = now() - interval '30 minutes'"
        )
    )
    resp = await _reset(client, token)
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID
    assert (await _user_row(db_session, user.id)).password_hash == user.password_hash


async def test_reset_superseded_token_422(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    old = await _issue(db_session, user)
    new = await _issue(db_session, user)
    resp = await _reset(client, old)
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID
    assert (await _reset(client, new)).status_code == 204


async def test_reset_verify_token_422(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session, verified=False)
    token = await _issue(db_session, user, "verify", timedelta(hours=24))
    resp = await _reset(client, token)
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID
    assert await _used_at(db_session, token) is None


async def test_reset_unknown_token_422(client: AsyncClient) -> None:
    resp = await _reset(client, secrets.token_urlsafe(32))
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID


async def test_reset_disabled_user_422_token_not_consumed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, status="disabled")
    token = await _issue(db_session, user)
    resp = await _reset(client, token)
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID
    assert await _used_at(db_session, token) is None
    assert (await _user_row(db_session, user.id)).password_hash == user.password_hash


@pytest.mark.parametrize(
    "body",
    [
        {"token": "too-short", "new_password": NEW_PASSWORD},
        {"token": "Bad!Chars" + "a" * 40, "new_password": NEW_PASSWORD},
        {"token": "a" * 43, "new_password": "x" * 129},
        {"token": "a" * 43},
        {"token": "a" * 43, "new_password": NEW_PASSWORD, "extra": True},
    ],
)
async def test_reset_malformed_token_422(client: AsyncClient, body: dict[str, Any]) -> None:
    resp = await client.post(RESET, json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert NEW_PASSWORD not in resp.text
    assert "x" * 129 not in resp.text


async def test_reset_supersedes_other_reset_and_verify_tokens(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, verified=False)
    verify = await _issue(db_session, user, "verify", timedelta(hours=24))
    token = await _issue(db_session, user)
    stray = secrets.token_urlsafe(32)  # a second unused reset token (not via `issue`)
    await db_session.execute(
        insert(EmailToken).values(
            id=uuid.uuid4(),
            user_id=user.id,
            purpose="reset",
            token_hash=sha256(stray),
            expires_at=func.now() + RESET_TTL,
        )
    )
    assert (await _reset(client, token)).status_code == 204
    assert await _used_at(db_session, verify) is not None
    assert await _used_at(db_session, stray) is not None
    assert (await _reset(client, stray)).json() == TOKEN_INVALID


async def test_reset_clears_login_failures(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session, email="locked.out@example.com")
    email_hmac = EmailHasher(HMAC_KEY).hmac(user.email)
    await db_session.execute(
        text(
            "INSERT INTO login_failures (email_hmac, failures, blocked_until) "
            "VALUES (:h, 7, now() + interval '15 minutes')"
        ),
        {"h": email_hmac},
    )
    token = await _issue(db_session, user)
    assert (await _reset(client, token)).status_code == 204
    assert await _count(db_session, LoginFailure) == 0
    resp = await client.post(LOGIN, json=login_body(user, NEW_PASSWORD), headers=auth_headers())
    assert resp.status_code == 200, resp.text


async def test_reset_sets_email_verified_at_if_null(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, verified=False)
    token = await _issue(db_session, user)
    assert (await _reset(client, token)).status_code == 204
    assert (await _user_row(db_session, user.id)).email_verified_at is not None


async def test_reset_keeps_existing_email_verified_at(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, verified=True)
    before = user.email_verified_at
    token = await _issue(db_session, user)
    assert (await _reset(client, token)).status_code == 204
    assert (await _user_row(db_session, user.id)).email_verified_at == before


async def test_reset_per_ip_429(make_client: MakeClient) -> None:
    async with make_client(reset_rate_limit=1) as c:
        first = await _reset(c, secrets.token_urlsafe(32))
        second = await _reset(c, secrets.token_urlsafe(32))
    assert first.status_code == 422
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(second.headers["Retry-After"]) <= 60


async def test_reset_logs_only_user_id(
    client: AsyncClient, db_session: AsyncSession, logs: pytest.LogCaptureFixture
) -> None:
    user = await make_login_user(db_session, email="quiet.person@example.com")
    token = await _issue(db_session, user)
    assert (await _reset(client, token)).status_code == 204
    assert f"password_reset user_id={user.id}" in logs.text
    assert "quiet.person" not in logs.text
    assert token not in logs.text
    assert NEW_PASSWORD not in logs.text
