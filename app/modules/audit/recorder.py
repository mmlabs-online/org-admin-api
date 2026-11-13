"""Audit placeholder (R4) until ADM-004 stores entries in the request transaction."""

import logging
import uuid
from typing import Protocol

logger = logging.getLogger(__name__)


class AuditRecorder(Protocol):
    async def record(
        self, event: str, *, actor_user_id: uuid.UUID, app_id: uuid.UUID, **ids: uuid.UUID
    ) -> None:
        """Record one audit event; ids only, never emails, tokens or cookies (R11)."""
        ...


class LogAuditRecorder:
    """Logs `audit event=… key=value`; replaced by an in-transaction insert in ADM-004."""

    async def record(
        self, event: str, *, actor_user_id: uuid.UUID, app_id: uuid.UUID, **ids: uuid.UUID
    ) -> None:
        fields = {"actor_user_id": actor_user_id, "app_id": app_id, **ids}
        logger.info("audit event=%s %s", event, " ".join(f"{k}={v}" for k, v in fields.items()))
