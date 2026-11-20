"""Attachment storage port (ADM-027, ADR-0018). ADM-030's service depends on this Protocol."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.modules.attachments.errors import DriveError, DriveErrorCode

if TYPE_CHECKING:
    import httpx

    from app.core.config import Settings

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


class ImageType(StrEnum):
    PNG = "image/png"
    JPEG = "image/jpeg"
    WEBP = "image/webp"
    GIF = "image/gif"


@dataclass(frozen=True)
class StoredFile:
    file_id: str  # never logged, never sent to a browser
    app_key: str | None
    attachment_id: UUID | None
    report_id: UUID | None
    created_at: datetime


async def read_capped(chunks: AsyncIterator[bytes], max_bytes: int = MAX_ATTACHMENT_BYTES) -> bytes:
    """Collect a stream; raise `too_large` as soon as the total exceeds the cap."""
    buf = bytearray()
    async for chunk in chunks:
        buf += chunk
        if len(buf) > max_bytes:
            raise DriveError(DriveErrorCode.TOO_LARGE)
    return bytes(buf)


class AttachmentStore(Protocol):
    configured: bool

    async def ensure_app_folder(self, app_key: str) -> str: ...

    async def create(
        self,
        *,
        app_key: str,
        report_id: UUID,
        attachment_id: UUID,
        content_type: ImageType,
        data: bytes,
    ) -> str: ...

    def open_read(self, file_id: str) -> AbstractAsyncContextManager[AsyncIterator[bytes]]: ...

    async def delete(self, file_id: str) -> None: ...

    def list_files(self) -> AsyncIterator[StoredFile]: ...

    async def aclose(self) -> None: ...


class DisabledAttachmentStore:
    """Used when no Drive keys are set: attachments are off, reports still flow (OQ35)."""

    configured = False

    async def ensure_app_folder(self, app_key: str) -> str:
        raise DriveError(DriveErrorCode.NOT_CONFIGURED)

    async def create(
        self,
        *,
        app_key: str,
        report_id: UUID,
        attachment_id: UUID,
        content_type: ImageType,
        data: bytes,
    ) -> str:
        raise DriveError(DriveErrorCode.NOT_CONFIGURED)

    def open_read(self, file_id: str) -> AbstractAsyncContextManager[AsyncIterator[bytes]]:
        raise DriveError(DriveErrorCode.NOT_CONFIGURED)

    async def delete(self, file_id: str) -> None:
        raise DriveError(DriveErrorCode.NOT_CONFIGURED)

    def list_files(self) -> AsyncIterator[StoredFile]:
        raise DriveError(DriveErrorCode.NOT_CONFIGURED)

    async def aclose(self) -> None:
        return None


def build_attachment_store(
    settings: "Settings", transport: "httpx.AsyncBaseTransport | None" = None
) -> AttachmentStore:
    if not settings.drive_enabled:
        return DisabledAttachmentStore()
    from app.modules.attachments.drive import GoogleDriveStore

    return GoogleDriveStore(settings, transport)
