"""Device sessions: cookie hashing, resolve with rotation, grace and reuse detection (ADR-0004)."""

import hashlib
import logging
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sessions.errors import SessionInvalidError
from app.modules.sessions.models import DeviceSession
from app.modules.sessions.repository import SessionRepository

logger = logging.getLogger(__name__)

ROTATE_AFTER = timedelta(hours=24)  # D6
USER_AGENT_MAX_LENGTH = 512


def new_device_token() -> str:
    """Opaque 256-bit cookie value."""
    return secrets.token_urlsafe(32)


def device_token_hash(raw: str) -> bytes:
    """A fast hash is enough for a 256-bit random secret."""
    return hashlib.sha256(raw.encode("utf-8")).digest()


@dataclass(frozen=True)
class ResolvedDevice:
    device_id: uuid.UUID
    new_cookie: str | None  # set when the cookie was rotated (send `Set-Cookie`)
    # Set when the request used the previous value within the grace window: only account
    # sessions created strictly before this instant may be used (fixation mitigation).
    accounts_created_before: datetime | None


class DeviceSessionService:
    def __init__(self, session: AsyncSession, *, idle_days: int, max_days: int) -> None:
        self.repo = SessionRepository(session)
        self.idle = timedelta(days=idle_days)
        self.max_age = timedelta(days=max_days)

    def _usable(self, device: DeviceSession, now: datetime) -> bool:
        return (
            device.revoked_at is None
            and device.last_seen_at >= now - self.idle
            and device.created_at >= now - self.max_age
        )

    async def resolve(
        self,
        cookies: Sequence[str],
        *,
        force_rotate: bool = False,
        commit_on_reuse: bool = True,
    ) -> ResolvedDevice:
        if not cookies:
            raise SessionInvalidError("Session is invalid")
        if len(cookies) > 1:
            logger.warning("mm_device_multiple")
            raise SessionInvalidError("Session is invalid")
        token_hash = device_token_hash(cookies[0])
        for _ in range(2):  # a lost rotation CAS re-reads once
            found = await self.repo.find_device(token_hash)
            if found is None:
                raise SessionInvalidError("Session is invalid")
            device, now = found
            if not self._usable(device, now):
                raise SessionInvalidError("Session is invalid")

            if device.token_hash == token_hash:
                if force_rotate or device.rotated_at <= now - ROTATE_AFTER:
                    new = new_device_token()
                    if await self.repo.rotate(device.id, token_hash, device_token_hash(new)):
                        return ResolvedDevice(device.id, new, None)
                    continue  # a concurrent request rotated first
                await self.repo.touch(device.id)
                return ResolvedDevice(device.id, None, None)

            # Previous value: accepted within the grace window, without re-rotation.
            if device.prev_valid_until is not None and now <= device.prev_valid_until:
                return ResolvedDevice(device.id, None, device.rotated_at)
            # Reuse after the grace window: revoke the whole device session.
            await self.repo.revoke_device(device.id)
            if commit_on_reuse:
                await self.repo.commit()
            logger.warning("device_session_reuse device_session_id=%s", device.id)
            raise SessionInvalidError("Session is invalid")
        raise SessionInvalidError("Session is invalid")

    async def start_for_login(
        self, cookies: Sequence[str], *, ip: str | None, user_agent: str | None
    ) -> ResolvedDevice:
        """Rotate a usable device session that has signed-in accounts; otherwise mint a new one.

        Never raises on a bad cookie. Reuse detected here revokes the old device in the login
        transaction itself.
        """
        try:
            resolved: ResolvedDevice | None = await self.resolve(
                cookies, force_rotate=True, commit_on_reuse=False
            )
        except SessionInvalidError:
            resolved = None
        if resolved is not None:
            if await self.repo.count_active_accounts(resolved.device_id) > 0:
                return resolved
            await self.repo.revoke_device(resolved.device_id)  # fixation: never reuse an empty one
        raw = new_device_token()
        device_id = await self.repo.add_device(
            device_token_hash(raw),
            ip=ip,
            user_agent=user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None,
        )
        return ResolvedDevice(device_id, raw, None)

    async def add_account(self, device_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
        return await self.repo.upsert_account(device_id, user_id)

    async def revoke_account(
        self, device_id: uuid.UUID, account_id: uuid.UUID, *, created_before: datetime | None
    ) -> None:
        await self.repo.revoke_account(device_id, account_id, created_before=created_before)

    async def revoke_device(self, device_id: uuid.UUID) -> None:
        await self.repo.revoke_device(device_id)
