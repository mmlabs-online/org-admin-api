"""Shared harness of the attachment API tests (ADM-030): a member with an open report, an app
wired to the in-memory fake Drive (never real Google). Fixtures are registered in `conftest.py`.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.attachments import drive as drive_mod
from app.modules.attachments.drive import GoogleDriveStore
from app.modules.bug_reports.models import BugReport, BugReportAttachment
from app.modules.rbac.models import App
from tests.auth_factories import ORIGIN, auth_headers
from tests.fake_drive import FakeDrive, drive_settings
from tests.test_bug_reports_submit import _app, _member

if TYPE_CHECKING:
    from tests.conftest import MakeStrictApp

SENTINEL = b"SENTINEL-IMAGE-CONTENT-7f3a"
IMAGES: dict[str, tuple[str, bytes]] = {
    "png": ("image/png", b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + SENTINEL),
    "jpeg": ("image/jpeg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + SENTINEL),
    "webp": ("image/webp", b"RIFF\x24\x00\x00\x00WEBPVP8 " + SENTINEL),
    "gif": ("image/gif", b"GIF89a\x01\x00\x01\x00" + SENTINEL),
}
PNG_TYPE, PNG = IMAGES["png"]


@pytest.fixture
def _quiet_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drive_mod, "_BACKOFF_BASE_SECONDS", 0.0)


@dataclass
class World:
    app: FastAPI
    client: AsyncClient
    fake: FakeDrive
    db: AsyncSession
    rbac_app: App
    raw: str
    user_id: uuid.UUID
    account_id: uuid.UUID
    device_id: uuid.UUID
    report_id: uuid.UUID

    def url(self, report_id: uuid.UUID | str | None = None) -> str:
        return f"/api/v1/auth/bug-reports/{report_id or self.report_id}/attachments"

    async def upload(
        self,
        data: bytes = PNG,
        ctype: str | None = PNG_TYPE,
        *,
        report_id: uuid.UUID | str | None = None,
        account_id: uuid.UUID | str | None = None,
        raw: str | None = None,
        app_key: str = "ricotdin",
        origin: str | None = ORIGIN,
        headers: dict[str, str] | None = None,
        client: AsyncClient | None = None,
    ) -> Response:
        h = auth_headers(app_key, origin, raw or self.raw)
        if ctype is not None:
            h["Content-Type"] = ctype
        h.update(headers or {})
        return await (client or self.client).post(
            self.url(report_id),
            params={"account_id": str(account_id or self.account_id)},
            content=data,
            headers=h,
        )

    async def rows(self) -> list[BugReportAttachment]:
        result = await self.db.scalars(
            select(BugReportAttachment)
            .where(BugReportAttachment.report_id == self.report_id)
            .execution_options(populate_existing=True)
        )
        return list(result)

    def drive_attachments(self) -> list[str]:
        return [
            fid
            for fid, f in self.fake.files.items()
            if f.get("appProperties", {}).get("kind") == "attachment"
        ]

    def seed_attachments(self, n: int) -> None:
        for i in range(n):
            self.db.add(
                BugReportAttachment(
                    report_id=self.report_id,
                    drive_file_id=f"seed{i}{uuid.uuid4().hex}",
                    content_type=PNG_TYPE,
                    size_bytes=len(PNG),
                )
            )


MakeWorld = Callable[..., AbstractAsyncContextManager[World]]


@pytest.fixture
def make_world(db_session: AsyncSession, make_strict_app: "MakeStrictApp") -> MakeWorld:
    @asynccontextmanager
    async def _make(
        admin_permissions: tuple[str, ...] | None = ("admin:*",), **settings: Any
    ) -> AsyncIterator[World]:
        rbac_app, role = await _app(db_session)
        user, raw, account = await _member(db_session, role)
        report = BugReport(app_id=rbac_app.id, user_id=user.id, description="broken")
        db_session.add(report)
        await db_session.flush()
        ids = (user.id, account.id, account.device_session_id, report.id)
        await db_session.commit()
        app = make_strict_app(
            admin_permissions=None if admin_permissions is None else list(admin_permissions),
            **settings,
        )
        fake = FakeDrive()
        store = GoogleDriveStore(drive_settings(), fake.transport)
        app.state.attachment_store = store
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield World(
                    app,
                    client,
                    fake,
                    db_session,
                    rbac_app,
                    raw,
                    ids[0],
                    ids[1],
                    ids[2],
                    ids[3],
                )
        finally:
            await store.aclose()

    return _make
