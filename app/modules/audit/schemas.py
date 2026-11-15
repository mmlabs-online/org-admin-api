import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict

from app.modules.audit.actions import AuditOutcome


def _str_or_none(value: Any) -> Any:
    return None if value is None else str(value)  # asyncpg returns `ipaddress` objects


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    actor_kind: str
    actor_user_id: uuid.UUID | None
    actor_label: str | None
    action: str
    outcome: AuditOutcome
    target_type: str | None
    target_id: uuid.UUID | None
    app_key: str | None
    org_id: uuid.UUID | None
    changes: dict[str, Any] | None
    ip: Annotated[str | None, BeforeValidator(_str_or_none)]
    user_agent: str | None
    request_id: str | None
