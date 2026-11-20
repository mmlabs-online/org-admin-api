import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import App
from app.modules.releases.models import AppRelease
from app.modules.releases.repository import AppReleaseRepository

RANDOM = "random"  # default `git_sha`; pass None for a record created from a report


async def make_release(
    session: AsyncSession,
    app: App,
    *,
    environment: str = "production",
    version: str | None = None,
    status: str = "pending",
    deployed_at: datetime | None = None,
    created_at: datetime | None = None,
    created_via: str = "app",
    git_sha: str | None = RANDOM,
    images: Sequence[dict[str, Any]] = (),
    changes: Sequence[dict[str, Any]] = (),
    **kwargs: Any,
) -> AppRelease:
    """Seed through the repository (the same path ADM-034 will use)."""
    sha = secrets.token_hex(20) if git_sha == RANDOM else git_sha
    if version is None:
        if environment == "staging":
            version = f"sha-{(sha or secrets.token_hex(20))[:7]}"
        else:
            version = (
                f"v{uuid.uuid4().int % 1000}.{uuid.uuid4().int % 1000}.{uuid.uuid4().int % 1000}"
            )
    fields: dict[str, Any] = {
        "app_id": app.id,
        "environment": environment,
        "version": version,
        "status": status,
        "deployed_at": deployed_at,
        "created_via": created_via,
        "git_sha": sha,
        "images": list(images),
        "changes": list(changes),
        **kwargs,
    }
    if created_at is not None:
        fields["created_at"] = created_at
    return await AppReleaseRepository(session).add(AppRelease(**fields))
