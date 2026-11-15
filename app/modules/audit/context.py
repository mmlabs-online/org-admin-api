"""HTTP-free request context and end-user actor for audit rows (R1)."""

import uuid
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class AuditContext:
    """Where a change came from; `AuditContext.NONE` for the CLI (no request)."""

    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    NONE: ClassVar["AuditContext"]


AuditContext.NONE = AuditContext()


@dataclass(frozen=True)
class EndUser:
    """A signed-in account acting for itself (join), not a staff principal."""

    user_id: uuid.UUID
