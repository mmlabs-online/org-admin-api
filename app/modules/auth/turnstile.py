"""Cloudflare Turnstile verification (fails closed)."""

import logging
from typing import Protocol

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# Cloudflare's published dummy secret keys (always pass / always fail / token spent).
_TEST_SECRET_PREFIXES = (
    "1x0000000000000000000000000000000",
    "2x0000000000000000000000000000000",
    "3x0000000000000000000000000000000",
)


class TurnstileVerifier(Protocol):
    async def verify(self, token: str, remote_ip: str) -> bool: ...


class CloudflareTurnstileVerifier:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport

    def _secret(self) -> str | None:
        key = self._settings.turnstile_secret_key
        secret = key.get_secret_value() if key else ""
        if not secret:
            return None
        if self._settings.app_env == "production" and secret.startswith(_TEST_SECRET_PREFIXES):
            return None
        return secret

    async def verify(self, token: str, remote_ip: str) -> bool:
        secret = self._secret()
        if secret is None:
            logger.error("turnstile secret not configured")
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0, transport=self._transport) as client:
                resp = await client.post(
                    SITEVERIFY_URL,
                    data={"secret": secret, "response": token, "remoteip": remote_ip},
                )
            if resp.status_code != 200:
                logger.warning("turnstile siteverify returned status %s", resp.status_code)
                return False
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("turnstile siteverify failed: %s", type(exc).__name__)
            return False
        if not isinstance(body, dict) or body.get("success") is not True:
            codes = body.get("error-codes") if isinstance(body, dict) else None
            logger.info("turnstile verification failed: %s", codes)
            return False
        allowed = self._settings.turnstile_allowed_hostnames
        if allowed and body.get("hostname") not in allowed:
            logger.info("turnstile hostname not allowed")
            return False
        return True
