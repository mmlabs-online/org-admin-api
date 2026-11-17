"""Insert app credentials through the test session (flush only; the test transaction rolls back)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.app_credentials.codec import new_credential, parse_credential, secret_hash
from app.modules.app_credentials.models import AppCredential
from app.modules.rbac.models import App


async def make_credential(
    session: AsyncSession,
    app: App,
    *,
    revoked: bool = False,
    created_by: uuid.UUID | None = None,
) -> tuple[AppCredential, str]:
    """Returns the row and the raw credential value."""
    key_id, raw = new_credential()
    parsed = parse_credential(raw)
    assert parsed is not None
    row = AppCredential(
        app_id=app.id,
        key_id=key_id,
        secret_hash=secret_hash(parsed[1]),
        created_by=created_by,
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row, raw
