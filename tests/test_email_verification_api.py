import asyncio
import logging
import re
import secrets
import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth import hooks as hooks_module
from app.modules.auth.models import EmailSendLog, EmailToken
from app.modules.auth.repository import EmailTokenRepository
from app.modules.auth.throttle import EmailHasher
from app.modules.email.dispatcher import EmailDispatcher
from app.modules.email.sender import OutgoingEmail
from app.modules.rbac.models import App
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
    parse_set_cookie,
    sha256,
    shift,
)
from tests.conftest import MakeClient
from tests.fakes import FakeEmailSender, FakeTurnstileVerifier
from tests.rbac_factories import assign, make_role

SIGNUP = "/api/v1/auth/signup"
VERIFY = "/api/v1/auth/verify-email"
RESEND = "/api/v1/auth/verify-email/resend"
TOKEN = "/api/v1/auth/token"
LINK_RE = re.compile(r"(https://\S+)/verify-email#token=([A-Za-z0-9_-]+)")
TOKEN_INVALID = {
    "error": {"code": "token_invalid", "message": "Token is invalid or expired", "details": None}
}


@pytest.fixture(autouse=True)
async def stoktok(db_session: AsyncSession) -> App:
    """Signup requires app context (USR-04c), so every test here has the `stoktok` app."""
    return await make_login_app(db_session)


def _signup_body(email: str = "dana@example.com") -> dict[str, Any]:
    return {"email": email, "password": PASSWORD, "turnstile_token": "pass"}


async def _signup(
    client: AsyncClient,
    dispatcher: EmailDispatcher,
    email: str = "dana@example.com",
    headers: dict[str, str] | None = None,
) -> Response:
    """Sign up from the `stoktok` app unless `headers` say otherwise."""
    headers = auth_headers() if headers is None else headers
    resp = await client.post(SIGNUP, json=_signup_body(email), headers=headers)
    await dispatcher.join()
    return resp


def _link(email: OutgoingEmail) -> tuple[str, str]:
    """(origin, token) of the verification link in the message body."""
    match = LINK_RE.search(email.body)
    assert match is not None
    return match.group(1), match.group(2)


async def _user(db_session: AsyncSession, resp: Response) -> User:
    user = await db_session.get(User, uuid.UUID(resp.json()["id"]))
    assert user is not None
    return user


async def _verified_at(db_session: AsyncSession, user_id: uuid.UUID) -> Any:
    return await db_session.scalar(select(User.email_verified_at).where(User.id == user_id))


async def _send_log_count(db_session: AsyncSession) -> int:
    return await db_session.scalar(select(func.count()).select_from(EmailSendLog)) or 0


async def _age_send_log(db_session: AsyncSession, delta: str = "-2 minutes") -> None:
    """Move every send into the past so the per-minute limit no longer applies."""
    await db_session.execute(
        text("UPDATE email_send_log SET sent_at = now() + CAST(CAST(:d AS text) AS interval)"),
        {"d": delta},
    )


async def _resend(
    client: AsyncClient, dispatcher: EmailDispatcher, account_id: uuid.UUID, cookie: str, **kw: Any
) -> Response:
    headers = auth_headers(cookie=cookie, **kw)
    resp = await client.post(RESEND, json={"account_id": str(account_id)}, headers=headers)
    await dispatcher.join()
    return resp


# --- sign-up sends the verification email -------------------------------------------------


async def test_signup_sends_verification_link_to_app_origin(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
) -> None:
    resp = await _signup(client, email_dispatcher, headers=auth_headers())
    assert resp.status_code == 201, resp.text
    (email,) = fake_email.sent
    assert email.to == "dana@example.com"
    assert email.purpose == "verify"
    assert str(email.user_id) == resp.json()["id"]
    origin, token = _link(email)
    assert origin == ORIGIN
    assert len(token) == 43
    assert f"{ORIGIN}/verify-email#token={token}" in email.body
    rows = (await db_session.execute(select(EmailSendLog.purpose, EmailSendLog.email_hmac))).all()
    assert [(r.purpose, r.email_hmac) for r in rows] == [
        ("verify", EmailHasher(HMAC_KEY).hmac("dana@example.com"))
    ]


@pytest.mark.parametrize(
    ("app_key", "origin", "code"),
    [
        (None, None, "app_header_required"),
        (None, ORIGIN, "app_header_required"),
        ("Bad Key!", ORIGIN, "app_header_required"),
        ("nope", ORIGIN, "origin_not_allowed"),
        ("frozen", ORIGIN, "origin_not_allowed"),
        ("stoktok", None, "origin_not_allowed"),
        ("stoktok", "null", "origin_not_allowed"),
        ("stoktok", "https://other.mmlabs.online", "origin_not_allowed"),
    ],
)
async def test_signup_bad_app_context_403_no_side_effects(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    fake_turnstile: FakeTurnstileVerifier,
    app_key: str | None,
    origin: str | None,
    code: str,
) -> None:
    await make_login_app(db_session, "frozen", status="disabled")
    resp = await _signup(client, email_dispatcher, headers=auth_headers(app_key, origin))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == code
    assert await db_session.scalar(select(func.count()).select_from(User)) == 0
    assert fake_turnstile.calls == []
    assert fake_email.calls == 0
    assert await _send_log_count(db_session) == 0


async def test_link_ignores_host_and_forwarded_host(
    client: AsyncClient,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
) -> None:
    evil = {"Host": "evil.example", "X-Forwarded-Host": "evil.example"}
    await _signup(client, email_dispatcher, "a@example.com", headers=evil | auth_headers())
    await _signup(client, email_dispatcher, "b@example.com", headers=auth_headers() | evil)
    first, second = fake_email.sent
    assert _link(first)[0] == ORIGIN
    assert _link(second)[0] == ORIGIN
    assert "evil" not in first.body + second.body


async def test_signup_failure_sends_nothing(
    make_strict_client: MakeClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    async with make_strict_client() as c:
        assert (await _signup(c, email_dispatcher)).status_code == 201
        assert (await _signup(c, email_dispatcher)).status_code == 409
        weak = await c.post(
            SIGNUP,
            json=_signup_body("weak@example.com") | {"password": "Password1234"},
            headers=auth_headers(),
        )
        assert weak.status_code == 422
        await email_dispatcher.join()
    assert len(fake_email.sent) == 1
    assert await _send_log_count(db_session) == 1
    assert await db_session.scalar(select(func.count()).select_from(EmailToken)) == 1


async def test_signup_response_does_not_wait_for_sender(
    client: AsyncClient, email_dispatcher: EmailDispatcher, fake_email: FakeEmailSender
) -> None:
    fake_email.gate = asyncio.Event()
    resp = await client.post(SIGNUP, json=_signup_body(), headers=auth_headers())
    assert resp.status_code == 201  # returned while the sender is still blocked
    assert fake_email.sent == []
    fake_email.gate.set()
    await email_dispatcher.join()
    assert len(fake_email.sent) == 1


async def test_token_stored_hashed_only(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    resp = await _signup(client, email_dispatcher)
    _, token = _link(fake_email.sent[0])
    row = (await db_session.execute(select(EmailToken))).scalar_one()
    assert row.token_hash == sha256(token)
    assert row.user_id == uuid.UUID(resp.json()["id"])
    assert row.purpose == "verify"
    assert row.used_at is None
    lifetime = await db_session.scalar(
        select(EmailToken.expires_at - EmailToken.created_at).where(EmailToken.id == row.id)
    )
    assert lifetime == timedelta(hours=24)
    dump = await db_session.scalar(text("SELECT string_agg(t::text, '') FROM email_tokens t"))
    assert token not in (dump or "")


# --- POST /auth/verify-email ----------------------------------------------------------------


async def test_verify_then_token_works(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
) -> None:
    signup = await _signup(client, email_dispatcher, headers=auth_headers())
    user = await _user(db_session, signup)
    login = await client.post(LOGIN, json=login_body(user), headers=auth_headers())
    assert login.status_code == 200, login.text
    cookie = parse_set_cookie(login)
    assert cookie is not None
    token_body = {"audience": "stoktok", "account_id": login.json()["account_id"]}
    headers = auth_headers(cookie=cookie.value)
    await assign(db_session, user, await make_role(db_session, stoktok, "stoktok:viewer"))

    before = await client.post(TOKEN, json=token_body, headers=headers)
    assert before.status_code == 403
    assert before.json()["error"]["code"] == "email_unverified"

    _, token = _link(fake_email.sent[0])
    resp = await client.post(VERIFY, json={"token": token})
    assert resp.status_code == 204
    assert resp.content == b""
    assert resp.headers["cache-control"] == "no-store"
    assert await _verified_at(db_session, user.id) is not None
    used_at = await db_session.scalar(select(EmailToken.used_at))
    assert used_at is not None

    after = await client.post(TOKEN, json=token_body, headers=headers)
    assert after.status_code == 200, after.text


@pytest.mark.parametrize("case", ["unknown", "superseded"])
async def test_verify_invalid_tokens_uniform_422(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    case: str,
) -> None:
    user = await _user(db_session, await _signup(client, email_dispatcher))
    _, token = _link(fake_email.sent[0])
    if case == "unknown":
        token = secrets.token_urlsafe(32)
    else:  # a newer token supersedes the emailed one
        await EmailTokenRepository(db_session).issue(
            user.id, "verify", sha256("newer"), timedelta(hours=1)
        )
    resp = await client.post(VERIFY, json={"token": token})
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID
    assert await _verified_at(db_session, user.id) is None


async def test_verify_used_token_invalid_when_not_verified(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await _user(db_session, await _signup(client, email_dispatcher))
    _, token = _link(fake_email.sent[0])
    await db_session.execute(text("UPDATE email_tokens SET used_at = now()"))
    resp = await client.post(VERIFY, json={"token": token})
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID
    assert await _verified_at(db_session, user.id) is None


async def test_verify_expired_token_invalid(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await _user(db_session, await _signup(client, email_dispatcher))
    _, token = _link(fake_email.sent[0])
    await db_session.execute(
        text(
            "UPDATE email_tokens SET created_at = now() - interval '25 hours', "
            "expires_at = now() - interval '1 hour'"
        )
    )
    resp = await client.post(VERIFY, json={"token": token})
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID
    assert await _verified_at(db_session, user.id) is None


async def test_verify_idempotent_when_already_verified(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await _user(db_session, await _signup(client, email_dispatcher))
    _, token = _link(fake_email.sent[0])
    assert (await client.post(VERIFY, json={"token": token})).status_code == 204
    first = await _verified_at(db_session, user.id)
    again = await client.post(VERIFY, json={"token": token})
    assert again.status_code == 204
    assert await _verified_at(db_session, user.id) == first


async def test_verify_disabled_user_token_invalid(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
) -> None:
    user = await _user(db_session, await _signup(client, email_dispatcher))
    _, token = _link(fake_email.sent[0])
    await db_session.execute(
        text("UPDATE users SET status = 'disabled' WHERE id = :id"), {"id": user.id}
    )
    resp = await client.post(VERIFY, json={"token": token})
    assert resp.status_code == 422
    assert resp.json() == TOKEN_INVALID
    assert await db_session.scalar(select(EmailToken.used_at)) is None
    assert await _verified_at(db_session, user.id) is None


@pytest.mark.parametrize(
    "body",
    [
        {"token": "too-short"},
        {"token": "x" * 129},
        {"token": "Bad!Chars" + "a" * 40},
        {"token": "a" * 43, "extra": True},
        {},
    ],
)
async def test_verify_malformed_token_422(client: AsyncClient, body: dict[str, Any]) -> None:
    resp = await client.post(VERIFY, json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    if "token" in body:
        assert body["token"] not in resp.text


async def test_verify_rate_limited_per_ip(make_client: MakeClient) -> None:
    async with make_client(verify_email_rate_limit=1) as c:
        first = await c.post(VERIFY, json={"token": secrets.token_urlsafe(32)})
        second = await c.post(VERIFY, json={"token": secrets.token_urlsafe(32)})
    assert first.status_code == 422
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(second.headers["Retry-After"]) <= 60


# --- POST /auth/verify-email/resend ---------------------------------------------------------


async def _signed_in(db_session: AsyncSession, **kwargs: Any) -> tuple[User, str, uuid.UUID]:
    user = await make_login_user(db_session, verified=False, **kwargs)
    raw, _, (account,) = await make_device(db_session, user)
    return user, raw, account.id


async def test_resend_ok_202(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
) -> None:
    user, raw, account_id = await _signed_in(db_session)
    resp = await _resend(client, email_dispatcher, account_id, raw)
    assert resp.status_code == 202, resp.text
    assert resp.content == b""
    assert resp.headers["cache-control"] == "no-store"
    assert parse_set_cookie(resp) is None
    (email,) = fake_email.sent
    assert email.to == user.email
    assert email.user_id == user.id
    assert _link(email)[0] == ORIGIN
    assert await _send_log_count(db_session) == 1


async def test_resend_forwards_rotated_cookie(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    stoktok: App,
) -> None:
    user = await make_login_user(db_session, verified=False)
    raw, device, (account,) = await make_device(db_session, user)
    await shift(db_session, "device_sessions", "rotated_at", device.id, "-25 hours")
    resp = await _resend(client, email_dispatcher, account.id, raw)
    assert resp.status_code == 202
    cookie = parse_set_cookie(resp)
    assert cookie is not None
    assert cookie.value != raw


async def test_resend_invalidates_previous_token(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
) -> None:
    user = await _user(db_session, await _signup(client, email_dispatcher))
    raw, _, (account,) = await make_device(db_session, user)
    await _age_send_log(db_session)
    resp = await _resend(client, email_dispatcher, account.id, raw)
    assert resp.status_code == 202
    old, new = (_link(e)[1] for e in fake_email.sent)
    assert (await client.post(VERIFY, json={"token": old})).json() == TOKEN_INVALID
    assert (await client.post(VERIFY, json={"token": new})).status_code == 204
    assert await _verified_at(db_session, user.id) is not None


async def test_resend_429_within_a_minute_of_signup(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
) -> None:
    user = await _user(db_session, await _signup(client, email_dispatcher))
    raw, _, (account,) = await make_device(db_session, user)
    resp = await _resend(client, email_dispatcher, account.id, raw)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(resp.headers["Retry-After"]) <= 60
    assert len(fake_email.sent) == 1  # only the sign-up email
    assert await _send_log_count(db_session) == 1
    assert await db_session.scalar(select(func.count()).select_from(EmailToken)) == 1


async def test_resend_429_after_five_a_day(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
) -> None:
    user, raw, account_id = await _signed_in(db_session)
    email_hmac = EmailHasher(HMAC_KEY).hmac(user.email)
    for hours in range(1, 6):  # 5 sends in the last 24 h, the latest an hour ago
        await db_session.execute(
            text(
                "INSERT INTO email_send_log (id, email_hmac, purpose, sent_at) VALUES "
                "(gen_random_uuid(), :h, 'verify', now() - make_interval(hours => :n))"
            ),
            {"h": email_hmac, "n": hours},
        )
    resp = await _resend(client, email_dispatcher, account_id, raw)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) == 19 * 3600  # until the oldest is 24 h old
    assert fake_email.calls == 0


async def test_resend_requires_cookie(
    client: AsyncClient, email_dispatcher: EmailDispatcher, stoktok: App
) -> None:
    resp = await client.post(RESEND, json={"account_id": str(uuid.uuid4())}, headers=auth_headers())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"


async def test_resend_foreign_account_401(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
) -> None:
    _, raw, _ = await _signed_in(db_session)
    _, _, other_account = await _signed_in(db_session)
    resp = await _resend(client, email_dispatcher, other_account, raw)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_invalid"
    assert parse_set_cookie(resp) is None  # other accounts on the device stay signed in
    assert fake_email.calls == 0


async def test_resend_disabled_user_401(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    stoktok: App,
) -> None:
    _, raw, account_id = await _signed_in(db_session, status="disabled")
    resp = await _resend(client, email_dispatcher, account_id, raw)
    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("app_key", "origin", "code"),
    [
        (None, ORIGIN, "app_header_required"),
        ("stoktok", "https://evil.mmlabs.online", "origin_not_allowed"),
        ("stoktok", None, "origin_not_allowed"),
    ],
)
async def test_resend_requires_app_header_and_origin(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
    app_key: str | None,
    origin: str | None,
    code: str,
) -> None:
    _, raw, account_id = await _signed_in(db_session)
    resp = await _resend(client, email_dispatcher, account_id, raw, app_key=app_key, origin=origin)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == code
    assert fake_email.calls == 0


async def test_resend_already_verified_409(
    client: AsyncClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
) -> None:
    user = await make_login_user(db_session, verified=True)
    raw, _, (account,) = await make_device(db_session, user)
    resp = await _resend(client, email_dispatcher, account.id, raw)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "email_already_verified"
    assert fake_email.calls == 0


async def test_resend_validation_422(
    client: AsyncClient, db_session: AsyncSession, stoktok: App
) -> None:
    _, raw, _ = await _signed_in(db_session)
    resp = await client.post(
        RESEND, json={"account_id": "not-a-uuid"}, headers=auth_headers(cookie=raw)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- global daily cap -------------------------------------------------------------------------


async def test_daily_cap_resend_429_and_signup_still_201(
    make_client: MakeClient,
    db_session: AsyncSession,
    email_dispatcher: EmailDispatcher,
    fake_email: FakeEmailSender,
    stoktok: App,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Alembic's `fileConfig` (session fixture) disables loggers created before it ran.
    monkeypatch.setattr(hooks_module.logger, "disabled", False)
    caplog.set_level(logging.INFO)
    async with make_client(email_daily_cap=1) as c:
        assert (await _signup(c, email_dispatcher, "first@example.com")).status_code == 201
        second = await _signup(c, email_dispatcher, "second@example.com")
        assert second.status_code == 201
        assert len(fake_email.sent) == 1
        assert "email_not_sent reason=daily_cap purpose=verify" in caplog.text
        assert "second@example.com" not in caplog.text

        user = await _user(db_session, second)
        raw, _, (account,) = await make_device(db_session, user)
        resp = await _resend(c, email_dispatcher, account.id, raw)
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "3600"
    assert len(fake_email.sent) == 1
