"""Insert deployer credentials through the test session (flush only; test rolls back)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deployer_credentials.codec import new_credential, parse_credential, secret_hash
from app.modules.deployer_credentials.models import DeployerCredential


async def make_deployer_credential(
    session: AsyncSession,
    *,
    revoked: bool = False,
    created_by: uuid.UUID | None = None,
) -> tuple[DeployerCredential, str]:
    """Returns the row and the raw credential value."""
    key_id, raw = new_credential()
    parsed = parse_credential(raw)
    assert parsed is not None
    row = DeployerCredential(
        key_id=key_id,
        secret_hash=secret_hash(parsed[1]),
        created_by=created_by,
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row, raw
