"""Google Drive attachment store against the fake Drive (ADM-027): no network, no real keys."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app
from app.modules.attachments import drive as drive_mod
from app.modules.attachments.drive import GoogleDriveStore, _q_literal
from app.modules.attachments.errors import DriveError, DriveErrorCode
from app.modules.attachments.store import (
    MAX_ATTACHMENT_BYTES,
    DisabledAttachmentStore,
    ImageType,
    build_attachment_store,
    read_capped,
)
from tests.fake_drive import (
    ACCESS_TOKEN,
    CLIENT_SECRET,
    GOOGLE_TEXT,
    REFRESH_TOKEN,
    ROOT_ID,
    FakeDrive,
    drive_settings,
    error_body,
)

REPORT = uuid.uuid4()
ATT = uuid.uuid4()


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drive_mod, "_BACKOFF_BASE_SECONDS", 0.0)
    # Alembic's `fileConfig` (test DB setup) disables existing loggers; the log asserts need them.
    monkeypatch.setattr(logging.getLogger(drive_mod.__name__), "disabled", False)


@pytest.fixture
def fake() -> FakeDrive:
    return FakeDrive()


@pytest.fixture
async def store(fake: FakeDrive) -> AsyncIterator[GoogleDriveStore]:
    s = GoogleDriveStore(drive_settings(), fake.transport)
    yield s
    await s.aclose()


async def _create(
    store: GoogleDriveStore, data: bytes = b"png-bytes", key: str = "ricotdin"
) -> str:
    return await store.create(
        app_key=key, report_id=REPORT, attachment_id=ATT, content_type=ImageType.PNG, data=data
    )


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for p in parts:
        yield p


# --- disabled / wiring ------------------------------------------------------------------------


async def test_build_store_disabled_when_not_configured() -> None:
    s = build_attachment_store(Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(s, DisabledAttachmentStore)
    assert s.configured is False
    calls: list[Any] = [
        s.ensure_app_folder("ricotdin"),
        s.create(
            app_key="ricotdin",
            report_id=REPORT,
            attachment_id=ATT,
            content_type=ImageType.PNG,
            data=b"x",
        ),
        s.delete("abc"),
    ]
    for call in calls:
        with pytest.raises(DriveError) as exc:
            await call
        assert exc.value.code == DriveErrorCode.NOT_CONFIGURED
    with pytest.raises(DriveError):
        s.open_read("abc")
    with pytest.raises(DriveError):
        s.list_files()
    await s.aclose()


async def test_create_app_logs_attachment_store_state(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Alembic's `fileConfig` (test DB setup) disables existing loggers.
    monkeypatch.setattr(logging.getLogger("app.main"), "disabled", False)
    caplog.set_level(logging.INFO, logger="app.main")
    app = create_app(Settings(_env_file=None, app_env="test"))  # type: ignore[call-arg]
    assert isinstance(app.state.attachment_store, DisabledAttachmentStore)
    assert "attachment_store=disabled" in caplog.text
    caplog.clear()
    app = create_app(drive_settings())
    assert app.state.attachment_store.configured is True
    assert "attachment_store=drive" in caplog.text
    await app.state.attachment_store.aclose()


# --- token ------------------------------------------------------------------------------------


async def test_token_refreshed_once_and_cached(fake: FakeDrive, store: GoogleDriveStore) -> None:
    await asyncio.gather(*(store.delete("abc") for _ in range(5)))
    assert fake.token_posts == 1
    assert REFRESH_TOKEN in fake.token_bodies[0]  # form body only
    for r in fake.requests:
        assert REFRESH_TOKEN not in str(r.url)
        assert REFRESH_TOKEN not in r.headers.get("authorization", "")
    await store.delete("abc")
    assert fake.token_posts == 1


async def test_token_invalid_grant_is_auth_failed(
    fake: FakeDrive, store: GoogleDriveStore, caplog: pytest.LogCaptureFixture
) -> None:
    fake.queue("token", httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(DriveError) as exc:
        await store.delete("abc")
    assert exc.value.code == DriveErrorCode.AUTH_FAILED
    assert "drive_auth_failed status=400" in caplog.text
    assert fake.count("delete") == 0


# --- folders ----------------------------------------------------------------------------------


async def test_ensure_app_folder_creates_when_missing_and_caches(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    folder = await store.ensure_app_folder("ricotdin")
    assert fake.files[folder]["parents"] == [ROOT_ID]
    assert fake.files[folder]["appProperties"] == {
        "mm_service": "org-admin-api",
        "kind": "app_folder",
        "app_key": "ricotdin",
    }
    n = len(fake.requests)
    assert await store.ensure_app_folder("ricotdin") == folder
    assert len(fake.requests) == n  # cached: no HTTP


async def test_ensure_app_folder_finds_existing(fake: FakeDrive, store: GoogleDriveStore) -> None:
    existing = fake.add_folder("ricotdin")
    assert await store.ensure_app_folder("ricotdin") == existing
    assert fake.count("folder") == 0


async def test_ensure_app_folder_picks_oldest_duplicate(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    newer = fake.add_folder("ricotdin", "2026-03-01T00:00:00.000Z")
    older = fake.add_folder("ricotdin", "2026-02-01T00:00:00.000Z")
    assert newer != older
    assert await store.ensure_app_folder("ricotdin") == older


@pytest.mark.parametrize("key", ["", "A", "Bad Key", "a'b", "../x", "x" * 33, "1abc"])
async def test_ensure_app_folder_rejects_bad_app_key(
    fake: FakeDrive, store: GoogleDriveStore, key: str
) -> None:
    with pytest.raises(DriveError) as exc:
        await store.ensure_app_folder(key)
    assert exc.value.code == DriveErrorCode.INVALID_INPUT
    assert fake.requests == []


def test_q_literal_escapes_quote_and_backslash() -> None:
    assert _q_literal("a'b\\c") == "a\\'b\\\\c"


# --- create -----------------------------------------------------------------------------------


async def test_create_multipart_body_and_properties(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    file_id = await _create(store, b"\x89PNGdata")
    uploads = [r for r in fake.requests if "/upload/" in str(r.url)]
    assert len(uploads) == 1
    assert uploads[0].headers["content-type"].startswith("multipart/related; boundary=")
    entry = fake.files[file_id]
    folder = await store.ensure_app_folder("ricotdin")
    assert entry["name"] == str(ATT)
    assert entry["parents"] == [folder]
    assert entry["mimeType"] == "image/png"
    assert entry["appProperties"] == {
        "mm_service": "org-admin-api",
        "kind": "attachment",
        "app_key": "ricotdin",
        "report_id": str(REPORT),
        "attachment_id": str(ATT),
    }
    assert entry["data"] == b"\x89PNGdata"


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (500, DriveErrorCode.UNAVAILABLE),
        (503, DriveErrorCode.UNAVAILABLE),
        (httpx.ReadTimeout("t"), DriveErrorCode.UNAVAILABLE),
        (401, DriveErrorCode.AUTH_FAILED),
        (429, DriveErrorCode.RATE_LIMITED),
    ],
)
async def test_create_never_retried(
    fake: FakeDrive, store: GoogleDriveStore, outcome: Any, code: DriveErrorCode
) -> None:
    fake.add_folder("ricotdin")
    fake.queue("create", outcome)
    with pytest.raises(DriveError) as exc:
        await _create(store)
    assert exc.value.code == code
    assert fake.count("create") == 1
    if outcome == 401:
        await store.delete("abc")  # token was invalidated: a new refresh happens
        assert fake.token_posts == 2


async def test_create_rejects_over_5mb_without_request(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    with pytest.raises(DriveError) as exc:
        await _create(store, b"x" * (MAX_ATTACHMENT_BYTES + 1))
    assert exc.value.code == DriveErrorCode.TOO_LARGE
    assert fake.requests == []
    fake.add_folder("ricotdin")
    await _create(store, b"x" * MAX_ATTACHMENT_BYTES)  # exactly 5 MiB passes


@pytest.mark.parametrize(
    ("status", "reason", "code"),
    [
        (403, "storageQuotaExceeded", DriveErrorCode.QUOTA_EXCEEDED),
        (403, "userRateLimitExceeded", DriveErrorCode.RATE_LIMITED),
        (403, "rateLimitExceeded", DriveErrorCode.RATE_LIMITED),
        (403, "forbidden", DriveErrorCode.REJECTED),
        (400, "badRequest", DriveErrorCode.REJECTED),
    ],
)
async def test_create_quota_exceeded(
    fake: FakeDrive, store: GoogleDriveStore, status: int, reason: str, code: DriveErrorCode
) -> None:
    fake.add_folder("ricotdin")
    fake.queue("create", httpx.Response(status, json=error_body(reason)))
    with pytest.raises(DriveError) as exc:
        await _create(store)
    assert exc.value.code == code
    assert fake.count("create") == 1


# --- read -------------------------------------------------------------------------------------


async def test_open_read_streams_bytes(fake: FakeDrive, store: GoogleDriveStore) -> None:
    fake.add_folder("ricotdin")
    file_id = await _create(store, b"hello")
    fake.read_chunks = [b"he", b"llo"]
    async with store.open_read(file_id) as chunks:
        assert await read_capped(chunks) == b"hello"
    assert fake.streams_closed == 1
    async with store.open_read(file_id) as chunks:
        async for _ in chunks:
            break  # early exit
    assert fake.streams_closed == 2


async def test_open_read_over_cap_raises_too_large(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    fake.add_folder("ricotdin")
    file_id = await _create(store)
    fake.read_chunks = [b"x" * MAX_ATTACHMENT_BYTES, b"y"]
    with pytest.raises(DriveError) as exc:
        async with store.open_read(file_id) as chunks:
            await read_capped(chunks, 10 * MAX_ATTACHMENT_BYTES)  # the store's own cap trips
    assert exc.value.code == DriveErrorCode.TOO_LARGE
    assert fake.streams_closed == 1


async def test_open_read_not_found(store: GoogleDriveStore) -> None:
    with pytest.raises(DriveError) as exc:
        async with store.open_read("missing"):
            pass
    assert exc.value.code == DriveErrorCode.NOT_FOUND


async def test_read_and_delete_retry_bounded(fake: FakeDrive, store: GoogleDriveStore) -> None:
    fake.add_folder("ricotdin")
    file_id = await _create(store, b"data")
    fake.queue("read", 503, 503)
    async with store.open_read(file_id) as chunks:
        assert await read_capped(chunks) == b"data"
    assert fake.count("read") == 3
    fake.queue("read", 503, 503, 503, 503)
    with pytest.raises(DriveError) as exc:
        async with store.open_read(file_id):
            pass
    assert exc.value.code == DriveErrorCode.UNAVAILABLE
    assert fake.count("read") == 6
    fake.queue("delete", 400)
    with pytest.raises(DriveError):
        await store.delete(file_id)
    assert fake.count("delete") == 1  # 400 is not retried


async def test_idempotent_call_refreshes_once_on_401(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    fake.add_folder("ricotdin")
    file_id = await _create(store)
    fake.queue("delete", 401)
    await store.delete(file_id)
    assert fake.token_posts == 2
    fake.queue("delete", 401, 401)
    with pytest.raises(DriveError) as exc:
        await store.delete("abc")
    assert exc.value.code == DriveErrorCode.AUTH_FAILED


async def test_delete_404_is_success(store: GoogleDriveStore) -> None:
    await store.delete("gone")


async def test_redirect_is_rejected(fake: FakeDrive, store: GoogleDriveStore) -> None:
    fake.queue("delete", httpx.Response(302, headers={"location": "https://evil.invalid/x"}))
    with pytest.raises(DriveError) as exc:
        await store.delete("abc")
    assert exc.value.code == DriveErrorCode.REJECTED
    assert fake.count("delete") == 1


@pytest.mark.parametrize("bad", ["../x", "a/b", "a?b", "", "a" * 129])
async def test_invalid_file_id_rejected_before_request(
    fake: FakeDrive, store: GoogleDriveStore, bad: str
) -> None:
    with pytest.raises(DriveError) as exc:
        await store.delete(bad)
    assert exc.value.code == DriveErrorCode.INVALID_INPUT
    with pytest.raises(DriveError):
        store.open_read(bad)
    assert fake.requests == []


async def test_read_capped_rejects_over_5mb_without_draining() -> None:
    served: list[int] = []

    async def gen() -> AsyncIterator[bytes]:
        for i in range(5):
            served.append(i)
            yield b"x" * (MAX_ATTACHMENT_BYTES // 2 + 1)

    with pytest.raises(DriveError) as exc:
        await read_capped(gen())
    assert exc.value.code == DriveErrorCode.TOO_LARGE
    assert served == [0, 1]
    assert len(await read_capped(_chunks(b"x" * MAX_ATTACHMENT_BYTES))) == MAX_ATTACHMENT_BYTES


# --- list -------------------------------------------------------------------------------------


async def test_list_files_pages_and_filters(fake: FakeDrive, store: GoogleDriveStore) -> None:
    fake.page_size = 1
    old = datetime.now(UTC) - timedelta(days=3)
    folder = fake.add_folder("ricotdin")
    other = fake.add_folder("tudo")
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    f1 = fake.add_file(parent=folder, attachment_id=a1, created=old)
    f2 = fake.add_file(parent=other, attachment_id=a2, created=old, app_key="tudo")
    f3 = fake.add_file(parent=folder, attachment_id=None, created=old, props={"mm_service": "x"})
    f4 = fake.add_file(
        parent=folder,
        attachment_id=None,
        created=old,
        props={"mm_service": "org-admin-api", "kind": "attachment"},
    )
    fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=old, props={"other": "y"})
    listed = {f.file_id: f async for f in store.list_files()}
    assert f1 in listed and f2 in listed
    assert listed[f1].attachment_id == a1
    assert listed[f2].app_key == "tudo"
    assert folder not in listed and other not in listed
    assert f3 not in listed  # no mm_service marker of ours: filtered by the query
    assert listed[f4].attachment_id is None  # marker but no attachment_id: listed, unparsable


# --- no leaks ---------------------------------------------------------------------------------


def _chain(exc: BaseException | None) -> list[BaseException]:
    out: list[BaseException] = []
    while exc is not None and exc not in out:
        out.append(exc)
        exc = exc.__cause__ or exc.__context__
    return out


async def test_no_sentinel_leaks_on_any_error_path(caplog: pytest.LogCaptureFixture) -> None:
    for name in ("", "httpx", "httpcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    sentinels = [
        REFRESH_TOKEN,
        CLIENT_SECRET,
        ACCESS_TOKEN,
        ROOT_ID,
        GOOGLE_TEXT,
        "googleapis.com",
        "fileSENTINELid",
    ]

    async def run(setup: Any, action: Any) -> None:
        fake = FakeDrive()
        s = GoogleDriveStore(drive_settings(), fake.transport)
        fake.add_folder("ricotdin")
        setup(fake)
        err: DriveError | None = None
        try:
            await action(s)
        except DriveError as exc:
            err = exc
        finally:
            await s.aclose()
        assert err is not None
        assert err.__cause__ is None and err.__context__ is None
        assert err.__suppress_context__ in (True, False)
        blob = f"{err!s}{err!r}{err.args}"
        for sentinel in sentinels:
            assert sentinel not in blob

    async def read(s: GoogleDriveStore) -> None:
        async with s.open_read("fileSENTINELid") as chunks:
            await read_capped(chunks)

    async def listing(s: GoogleDriveStore) -> None:
        async for _ in s.list_files():
            pass

    bad_json = httpx.Response(200, content=b"not json " + GOOGLE_TEXT.encode())
    for status in (400, 401, 500):
        await run(
            lambda f, st=status: f.queue("token", st, st, st), lambda s: s.delete("fileSENTINELid")
        )
    await run(
        lambda f: f.queue("token", bad_json, bad_json, bad_json),
        lambda s: s.delete("fileSENTINELid"),
    )
    for outcome in (400, 403, 500, httpx.ConnectTimeout(GOOGLE_TEXT)):
        await run(lambda f, o=outcome: f.queue("create", o), lambda s: _create(s))
    await run(lambda f: f.queue("create", bad_json), lambda s: _create(s))
    await run(lambda f: None, read)
    await run(lambda f: f.queue("read", 500, 500, 500), read)
    await run(lambda f: f.queue("delete", 500, 500, 500), lambda s: s.delete("fileSENTINELid"))
    await run(lambda f: f.queue("list", 500, 500, 500), listing)
    await run(lambda f: f.queue("list", bad_json), listing)
    for sentinel in sentinels:
        assert sentinel not in caplog.text
