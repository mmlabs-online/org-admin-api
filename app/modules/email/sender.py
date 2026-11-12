"""Outgoing email behind a swappable `EmailSender` (D3, ADR-0007).

`GmailSmtpSender` speaks SMTP with mandatory STARTTLS (certificate and hostname checked). Errors
are mapped to `EmailDeliveryError` kinds without the SMTP reply text, which echoes the recipient
(R11). Switching provider = a new `EmailSender` and a branch in `build_email_sender`.
"""

import asyncio
import logging
import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr
from typing import Literal, Protocol

from pydantic import SecretStr

from app.core.config import Settings

logger = logging.getLogger(__name__)

DeliveryErrorKind = Literal["transient", "permanent", "quota", "auth"]
SENDER_NAME = "mmlabs"
SMTP_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class OutgoingEmail:
    to: str = field(repr=False)
    subject: str = field(repr=False)
    body: str = field(repr=False)  # holds the one-time link
    purpose: str
    user_id: uuid.UUID


class EmailDeliveryError(Exception):
    def __init__(self, kind: DeliveryErrorKind, smtp_code: int | None = None) -> None:
        super().__init__(f"email delivery failed: {kind}")
        self.kind: DeliveryErrorKind = kind
        self.smtp_code = smtp_code


class EmailSender(Protocol):
    async def send(self, email: OutgoingEmail) -> None:
        """Deliver one message or raise `EmailDeliveryError`."""
        ...


def _classify_code(code: int, reply: bytes = b"") -> EmailDeliveryError:
    if code == 550 and b"5.4.5" in reply:  # Gmail daily sending limit
        return EmailDeliveryError("quota", code)
    if 400 <= code < 500:
        return EmailDeliveryError("transient", code)
    return EmailDeliveryError("permanent", code)


def classify_smtp_error(exc: BaseException) -> EmailDeliveryError:
    if isinstance(exc, smtplib.SMTPNotSupportedError):  # e.g. STARTTLS not offered
        return EmailDeliveryError("permanent")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return EmailDeliveryError("auth", exc.smtp_code)
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in exc.recipients.values()]
        return _classify_code(codes[0]) if len(codes) == 1 else EmailDeliveryError("permanent")
    if isinstance(exc, smtplib.SMTPResponseException):
        reply = exc.smtp_error if isinstance(exc.smtp_error, bytes) else b""
        return _classify_code(exc.smtp_code, reply)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return EmailDeliveryError("permanent")
    if isinstance(exc, smtplib.SMTPServerDisconnected | TimeoutError | OSError):
        return EmailDeliveryError("transient")
    return EmailDeliveryError("permanent")


class GmailSmtpSender:
    """`smtp.gmail.com:587`, STARTTLS required, App Password login (stdlib `smtplib`)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: SecretStr,
        from_address: str,
        timeout: float = SMTP_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from = from_address
        self._timeout = timeout

    async def send(self, email: OutgoingEmail) -> None:
        msg = EmailMessage()  # refuses CR/LF in headers
        msg["From"] = formataddr((SENDER_NAME, self._from))
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg.set_content(email.body)
        await asyncio.to_thread(self._send_sync, msg)

    def _send_sync(self, msg: EmailMessage) -> None:
        error: EmailDeliveryError | None = None
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(self._username, self._password.get_secret_value())
                smtp.send_message(msg)
        except Exception as exc:  # mapped below, outside the handler: no chained reply text
            error = classify_smtp_error(exc)
        if error is not None:
            raise error


class DisabledEmailSender:
    """Local/test without SMTP keys (refused in staging/production by `Settings`)."""

    async def send(self, email: OutgoingEmail) -> None:
        logger.warning(
            "email_not_sent reason=smtp_unconfigured purpose=%s user_id=%s",
            email.purpose,
            email.user_id,
        )


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.smtp_username and settings.smtp_password:
        return GmailSmtpSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            from_address=settings.email_from or settings.smtp_username,
        )
    return DisabledEmailSender()
