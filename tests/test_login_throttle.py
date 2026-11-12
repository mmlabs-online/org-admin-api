from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import LoginFailure
from app.modules.auth.passwords import PasswordHasher
from app.modules.auth.throttle import EmailHasher, backoff_minutes
from tests.auth_factories import (
    HMAC_KEY,
    LOGIN,
    PASSWORD,
    auth_headers,
    login_body,
    make_login_app,
    make_login_user,
)
from tests.conftest import MakeClient
from tests.fakes import FakeTurnstileVerifier

WRONG = "wrong password entirely"


@pytest.fixture(autouse=True)
async def _app(db_session: AsyncSession) -> None:
    await make_login_app(db_session)


def _hmac(email: str) -> bytes:
    return EmailHasher(HMAC_KEY).hmac(email.lower())


async def _row(db_session: AsyncSession, email: str) -> LoginFailure | None:
    return await db_session.scalar(
        select(LoginFailure)
        .where(LoginFailure.email_hmac == _hmac(email))
        .execution_options(populate_existing=True)
    )


async def _set_failures(
    db_session: AsyncSession, email: str, failures: int, *, last_failed: str = "0 seconds"
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO login_failures (email_hmac, failures, last_failed_at) "
            "VALUES (:h, :f, now() + CAST(CAST(:d AS text) AS interval))"
        ),
        {"h": _hmac(email), "f": failures, "d": last_failed},
    )


async def _post(client: AsyncClient, email: str, password: str = WRONG, **extra: Any) -> Response:
    return await client.post(
        LOGIN, json={"email": email, "password": password, **extra}, headers=auth_headers()
    )


@pytest.mark.parametrize(
    ("failures", "minutes"),
    [(0, None), (4, None), (5, 1), (6, 2), (7, 4), (8, 8), (9, 15), (50, 15)],
)
def test_backoff_minutes(failures: int, minutes: int | None) -> None:
    assert backoff_minutes(failures) == minutes


async def test_fifth_failure_blocks_one_minute(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    for _ in range(5):
        resp = await _post(client, user.email, turnstile_token="pass")
        assert resp.status_code == 401
    resp = await _post(client, user.email, PASSWORD, turnstile_token="pass")
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(resp.headers["retry-after"]) <= 60
    assert resp.json()["error"]["details"] == {"retry_after": int(resp.headers["retry-after"])}


@pytest.mark.parametrize(("before", "minutes"), [(4, 1), (5, 2), (7, 8), (8, 15), (20, 15)])
async def test_backoff_doubles_and_caps(
    client: AsyncClient, db_session: AsyncSession, before: int, minutes: int
) -> None:
    user = await make_login_user(db_session)
    await _set_failures(db_session, user.email, before)
    resp = await _post(client, user.email, turnstile_token="pass")
    assert resp.status_code == 401
    row = await _row(db_session, user.email)
    assert row is not None and row.failures == before + 1
    assert await db_session.scalar(
        text(
            "SELECT blocked_until = now() + make_interval(mins => :m) FROM login_failures "
            "WHERE email_hmac = :h"
        ),
        {"m": minutes, "h": _hmac(user.email)},
    )


async def test_blocked_email_skips_password_check(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await make_login_user(db_session)
    await _set_failures(db_session, user.email, 6)
    await db_session.execute(
        text(
            "UPDATE login_failures SET blocked_until = now() + interval '5 minutes' "
            "WHERE email_hmac = :h"
        ),
        {"h": _hmac(user.email)},
    )
    calls: list[str] = []

    async def spy(*args: object) -> bool:
        calls.append("verify")
        return True

    monkeypatch.setattr(PasswordHasher, "verify", spy)
    monkeypatch.setattr(PasswordHasher, "verify_dummy", spy)
    resp = await _post(client, user.email, PASSWORD, turnstile_token="pass")
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "300"
    assert calls == []
    row = await _row(db_session, user.email)
    assert row is not None and row.failures == 6


async def test_turnstile_required_after_three_failures(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    for _ in range(3):
        assert (await _post(client, user.email)).status_code == 401
    resp = await _post(client, user.email, PASSWORD)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "turnstile_required"
    resp = await _post(client, user.email, PASSWORD, turnstile_token="pass")
    assert resp.status_code == 200


async def test_turnstile_invalid_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    await _set_failures(db_session, user.email, 3)
    resp = await _post(client, user.email, PASSWORD, turnstile_token="nope")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "turnstile_failed"
    row = await _row(db_session, user.email)
    assert row is not None and row.failures == 3


async def test_turnstile_not_called_below_threshold(
    client: AsyncClient, db_session: AsyncSession, fake_turnstile: FakeTurnstileVerifier
) -> None:
    user = await make_login_user(db_session)
    for _ in range(3):
        assert (await _post(client, user.email, turnstile_token="pass")).status_code == 401
    assert fake_turnstile.calls == []
    assert (await _post(client, user.email, turnstile_token="pass")).status_code == 401
    assert len(fake_turnstile.calls) == 1


async def test_unknown_email_counted_like_known(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)

    async def sequence(email: str) -> list[tuple[int, Any, str | None]]:
        out = []
        for i in range(7):
            resp = await _post(client, email, turnstile_token="pass" if i >= 3 else None)
            out.append((resp.status_code, resp.json(), resp.headers.get("retry-after")))
        return out

    known = await sequence(user.email)
    unknown = await sequence("nobody@example.com")
    assert known == unknown
    assert [s for s, _, _ in known] == [401, 401, 401, 401, 401, 429, 429]
    row = await _row(db_session, "nobody@example.com")
    assert row is not None and row.failures == 5


async def test_success_resets_counter(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    for _ in range(2):
        assert (await _post(client, user.email)).status_code == 401
    assert await _row(db_session, user.email) is not None
    assert (await _post(client, user.email, PASSWORD)).status_code == 200
    assert await _row(db_session, user.email) is None


async def test_counter_resets_after_24h(client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    await _set_failures(db_session, user.email, 4, last_failed="-25 hours")
    resp = await _post(client, user.email)  # no Turnstile needed: the old counter is stale
    assert resp.status_code == 401
    row = await _row(db_session, user.email)
    assert row is not None and row.failures == 1


async def test_failure_counter_persists_despite_401(
    make_strict_client: MakeClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session)
    email = user.email
    await db_session.commit()
    async with make_strict_client() as client:
        resp = await _post(client, email)
    assert resp.status_code == 401
    row = await _row(db_session, email)
    assert row is not None and row.failures == 1


async def test_login_per_ip_limit(make_client: MakeClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session)
    async with make_client(login_rate_limit=2) as client:
        assert (
            await client.post(LOGIN, json=login_body(user), headers=auth_headers())
        ).status_code == 200
        assert (await _post(client, user.email)).status_code == 401
        resp = await _post(client, user.email, PASSWORD)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    assert "retry-after" in resp.headers
