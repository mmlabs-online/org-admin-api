import logging
import uuid
from collections.abc import AsyncIterator, Callable

import pytest

from app.modules.email import dispatcher as dispatcher_module
from app.modules.email.dispatcher import EmailDispatcher
from app.modules.email.sender import EmailDeliveryError, OutgoingEmail
from tests.fakes import FakeEmailSender

ADDRESS = "carol.private@example.com"
TOKEN = "DispatcherSentinelToken0123456789abcdefghij"
LINK = f"https://app.mmlabs.online/verify-email#token={TOKEN}"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    # Alembic's `fileConfig` (session fixture) disables loggers created before it ran.
    monkeypatch.setattr(dispatcher_module.logger, "disabled", False)
    caplog.set_level(logging.DEBUG)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def clock() -> Clock:
    return Clock()


Make = Callable[..., EmailDispatcher]


@pytest.fixture
async def make(sleeps: list[float], clock: Clock) -> AsyncIterator[Make]:
    created: list[EmailDispatcher] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    def _make(sender: FakeEmailSender, maxsize: int = 1000) -> EmailDispatcher:
        d = EmailDispatcher(sender, sleep=_sleep, clock=clock, maxsize=maxsize)
        created.append(d)
        return d

    yield _make
    for d in created:
        await d.aclose()


def _email() -> OutgoingEmail:
    return OutgoingEmail(
        to=ADDRESS, subject="Verify", body=f"Open {LINK}", purpose="verify", user_id=uuid.uuid4()
    )


async def _send(d: EmailDispatcher, email: OutgoingEmail | None = None) -> OutgoingEmail:
    email = email or _email()
    await d.enqueue(email)
    await d.join()
    return email


async def test_transient_then_success(make: Make, sleeps: list[float]) -> None:
    sender = FakeEmailSender()
    sender.failures = [EmailDeliveryError("transient", 421)]
    email = await _send(make(sender))
    assert sender.calls == 2
    assert sender.sent == [email]
    assert len(sleeps) == 1
    assert 1.6 <= sleeps[0] <= 2.4  # 2 s +/- 20 %


async def test_gives_up_after_max_attempts(
    make: Make,
    sleeps: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = FakeEmailSender()
    sender.failures = [EmailDeliveryError("transient")] * 3
    d = make(sender)
    await _send(d)
    assert sender.calls == 3
    assert sender.sent == []
    assert len(sleeps) == 2
    assert 6.4 <= sleeps[1] <= 9.6  # 8 s +/- 20 %
    assert "email_failed kind=transient attempt=3" in caplog.text
    nxt = await _send(d)  # the worker keeps running
    assert sender.sent == [nxt]


async def test_permanent_not_retried(make: Make, caplog: pytest.LogCaptureFixture) -> None:
    sender = FakeEmailSender()
    sender.failures = [EmailDeliveryError("permanent", 554)]
    await _send(make(sender))
    assert sender.calls == 1
    assert "email_failed kind=permanent attempt=1 smtp_code=554" in caplog.text


async def test_unexpected_error_does_not_kill_worker(
    make: Make,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = FakeEmailSender()
    sender.failures = [ValueError(ADDRESS)]
    d = make(sender)
    await _send(d)
    assert "email_failed reason=unexpected error=ValueError" in caplog.text
    nxt = await _send(d)
    assert sender.sent == [nxt]


async def test_quota_error_pauses_and_drops(
    make: Make,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = FakeEmailSender()
    sender.failures = [EmailDeliveryError("quota", 550)]
    d = make(sender)
    await _send(d)
    assert sender.calls == 1
    assert "email_quota_exceeded attempt=1 smtp_code=550" in caplog.text

    await _send(d)
    assert sender.calls == 1  # dropped while paused
    assert "email_dropped reason=paused" in caplog.text

    clock.now += 3601
    after = await _send(d)
    assert sender.sent == [after]


async def test_auth_error_pauses(
    make: Make,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = FakeEmailSender()
    sender.failures = [EmailDeliveryError("auth", 535)]
    d = make(sender)
    await _send(d)
    assert "email_auth_failed attempt=1 smtp_code=535" in caplog.text
    await _send(d)
    assert sender.calls == 1
    clock.now += 3601
    await _send(d)
    assert len(sender.sent) == 1


async def test_queue_full_drops(make: Make, caplog: pytest.LogCaptureFixture) -> None:
    sender = FakeEmailSender()
    d = make(sender, maxsize=1)
    first = _email()
    await d.enqueue(first)  # the worker has not run yet: the queue is now full
    await d.enqueue(_email())  # no exception to the caller
    assert "email_dropped reason=queue_full" in caplog.text
    await d.join()
    assert sender.sent == [first]


async def test_logs_never_contain_token_link_or_address(
    make: Make,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = FakeEmailSender()
    sender.failures = [
        EmailDeliveryError("transient", 421),  # retry, then sent
        EmailDeliveryError("permanent", 554),  # failed
        ValueError(ADDRESS),  # unexpected
        EmailDeliveryError("quota", 550),  # pause
    ]
    d = make(sender, maxsize=5)
    for _ in range(5):
        await _send(d)  # sent, failed, unexpected, quota, dropped (paused)
    clock.now += 3601
    for _ in range(6):
        await d.enqueue(_email())  # one dropped: queue full
    await d.join()
    for event in [
        "email_retry",
        "email_sent",
        "email_failed",
        "email_quota_exceeded",
        "email_dropped reason=paused",
        "email_dropped reason=queue_full",
    ]:
        assert event in caplog.text
    for secret in [ADDRESS, "carol.private", TOKEN, LINK, "example.com"]:
        assert secret not in caplog.text
