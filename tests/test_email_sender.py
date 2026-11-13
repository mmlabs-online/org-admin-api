import smtplib
import ssl
import uuid
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.modules.email import sender as sender_module
from app.modules.email.sender import (
    DisabledEmailSender,
    EmailDeliveryError,
    GmailSmtpSender,
    OutgoingEmail,
    build_email_sender,
)

ADDRESS = "bob.secret@example.com"
LINK = "https://app.mmlabs.online/verify-email#token=SentinelToken0123456789abcdefghijklmnopq"


class FakeSMTP:
    """Stands in for `smtplib.SMTP`; `fail` maps a method name to the exception it raises."""

    instances: ClassVar[list["FakeSMTP"]] = []
    fail: ClassVar[dict[str, BaseException]] = {}

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.args = (host, port, timeout)
        self.calls: list[str] = []
        self.context: ssl.SSLContext | None = None
        self.login_args: tuple[str, str] | None = None
        self.message: EmailMessage | None = None
        FakeSMTP.instances.append(self)
        self._maybe_fail("connect")

    def _maybe_fail(self, name: str) -> None:
        if name in FakeSMTP.fail:
            raise FakeSMTP.fail[name]

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("quit")

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, *, context: ssl.SSLContext) -> None:
        self.calls.append("starttls")
        self.context = context
        self._maybe_fail("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.login_args = (user, password)
        self._maybe_fail("login")

    def send_message(self, msg: EmailMessage) -> None:
        self.calls.append("send_message")
        self.message = msg
        self._maybe_fail("send_message")


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.fail = {}
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def _sender() -> GmailSmtpSender:
    return GmailSmtpSender(
        host="smtp.gmail.com",
        port=587,
        username="sender@mmlabs.test",
        password=SecretStr("app-password"),
        from_address="sender@mmlabs.test",
    )


def _email() -> OutgoingEmail:
    return OutgoingEmail(
        to=ADDRESS, subject="Verify", body=f"Open {LINK}", purpose="verify", user_id=uuid.uuid4()
    )


async def test_gmail_sender_starttls_with_default_context_and_login() -> None:
    await _sender().send(_email())
    (smtp,) = FakeSMTP.instances
    assert smtp.args == ("smtp.gmail.com", 587, 15.0)
    assert smtp.calls == ["ehlo", "starttls", "ehlo", "login", "send_message", "quit"]
    assert smtp.context is not None
    assert smtp.context.check_hostname is True
    assert smtp.context.verify_mode == ssl.CERT_REQUIRED
    assert smtp.login_args == ("sender@mmlabs.test", "app-password")
    assert smtp.message is not None
    assert smtp.message["From"] == "mmlabs <sender@mmlabs.test>"
    assert smtp.message["To"] == ADDRESS
    assert smtp.message["Subject"] == "Verify"
    assert LINK in smtp.message.get_content()


async def test_starttls_not_offered_is_permanent() -> None:
    FakeSMTP.fail = {"starttls": smtplib.SMTPNotSupportedError("STARTTLS extension not supported")}
    with pytest.raises(EmailDeliveryError) as exc:
        await _sender().send(_email())
    assert exc.value.kind == "permanent"
    assert "login" not in FakeSMTP.instances[0].calls
    assert "send_message" not in FakeSMTP.instances[0].calls


async def test_cert_error_is_permanent() -> None:
    FakeSMTP.fail = {"starttls": ssl.SSLCertVerificationError("certificate verify failed")}
    with pytest.raises(EmailDeliveryError) as exc:
        await _sender().send(_email())
    assert exc.value.kind == "permanent"
    assert "send_message" not in FakeSMTP.instances[0].calls


@pytest.mark.parametrize(
    ("method", "error", "kind", "code"),
    [
        (
            "send_message",
            smtplib.SMTPResponseException(421, b"4.7.0 Try again later"),
            "transient",
            421,
        ),
        ("send_message", smtplib.SMTPDataError(451, b"4.3.0 Temporary failure"), "transient", 451),
        (
            "send_message",
            smtplib.SMTPDataError(550, b"5.4.5 Daily user sending quota exceeded"),
            "quota",
            550,
        ),
        (
            "login",
            smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted"),
            "auth",
            535,
        ),
        (
            "send_message",
            smtplib.SMTPDataError(554, f"5.7.0 rejected {ADDRESS}".encode()),
            "permanent",
            554,
        ),
        (
            "send_message",
            smtplib.SMTPRecipientsRefused({ADDRESS: (450, b"4.2.1 mailbox busy")}),
            "transient",
            450,
        ),
        (
            "send_message",
            smtplib.SMTPRecipientsRefused({ADDRESS: (550, b"5.1.1 no such user")}),
            "permanent",
            550,
        ),
        ("connect", TimeoutError("timed out"), "transient", None),
        ("connect", ConnectionRefusedError("refused"), "transient", None),
        ("send_message", smtplib.SMTPServerDisconnected("gone"), "transient", None),
        ("connect", OSError("no route"), "transient", None),
    ],
)
async def test_classification(method: str, error: BaseException, kind: str, code: Any) -> None:
    FakeSMTP.fail = {method: error}
    with pytest.raises(EmailDeliveryError) as exc:
        await _sender().send(_email())
    assert exc.value.kind == kind
    assert exc.value.smtp_code == code
    # No chained SMTP reply (it echoes the recipient) in tracebacks or logs.
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert ADDRESS not in str(exc.value)
    assert ADDRESS not in repr(exc.value)


def test_outgoing_email_repr_hides_secrets() -> None:
    text = repr(_email())
    assert ADDRESS not in text
    assert "bob.secret" not in text
    assert LINK not in text
    assert "SentinelToken" not in text
    assert "purpose='verify'" in text


def test_build_email_sender() -> None:
    assert isinstance(build_email_sender(Settings()), GmailSmtpSender)
    disabled = build_email_sender(Settings(smtp_username=None, smtp_password=None))
    assert isinstance(disabled, DisabledEmailSender)


async def test_disabled_sender_logs_without_address(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Alembic's `fileConfig` (session fixture) disables loggers created before it ran.
    monkeypatch.setattr(sender_module.logger, "disabled", False)
    caplog.set_level("DEBUG")
    await DisabledEmailSender().send(_email())
    assert "email_not_sent reason=smtp_unconfigured" in caplog.text
    assert ADDRESS not in caplog.text
    assert "SentinelToken" not in caplog.text
