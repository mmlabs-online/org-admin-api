"""Attachment sweep service (ADM-027): fail-closed, only old unreferenced files."""

import uuid
from collections.abc import AsyncIterator, Collection
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.attachments import drive as drive_mod
from app.modules.attachments.drive import GoogleDriveStore
from app.modules.attachments.errors import DriveErrorCode
from app.modules.attachments.sweep import ReferenceChecker, SweepRefused, SweepService
from tests.fake_drive import FakeDrive, drive_settings

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(hours=72)
YOUNG = NOW - timedelta(hours=2)


class Checker:
    def __init__(self, referenced: set[uuid.UUID], *, fail: bool = False) -> None:
        self._referenced = referenced
        self._fail = fail

    async def referenced(self, attachment_ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
        if self._fail:
            raise RuntimeError("postgresql://user:secret@db/x")
        return self._referenced & set(attachment_ids)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drive_mod, "_BACKOFF_BASE_SECONDS", 0.0)


@pytest.fixture
def fake() -> FakeDrive:
    return FakeDrive()


@pytest.fixture
async def store(fake: FakeDrive) -> AsyncIterator[GoogleDriveStore]:
    s = GoogleDriveStore(drive_settings(), fake.transport)
    yield s
    await s.aclose()


def _service(
    store: GoogleDriveStore, checker: ReferenceChecker | None, hours: int = 48
) -> SweepService:
    return SweepService(store, checker, grace=timedelta(hours=hours), now=lambda: NOW)


async def test_dry_run_lists_candidates_deletes_nothing(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    folder = fake.add_folder("ricotdin")
    old_id = uuid.uuid4()
    fake.add_file(parent=folder, attachment_id=old_id, created=OLD)
    fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=YOUNG)
    result = await _service(store, Checker(set())).run(apply=False, max_deletes=10)
    assert [c.attachment_id for c in result.candidates] == [old_id]
    assert result.candidates[0].age_hours == 72
    assert result.candidates[0].app_key == "ricotdin"
    assert result.deleted == 0 and result.references_checked is True
    assert fake.count("delete") == 0


async def test_apply_deletes_only_old_unreferenced(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    folder = fake.add_folder("ricotdin")
    orphan, referenced = uuid.uuid4(), uuid.uuid4()
    orphan_file = fake.add_file(parent=folder, attachment_id=orphan, created=OLD)
    ref_file = fake.add_file(parent=folder, attachment_id=referenced, created=OLD)
    young_file = fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=YOUNG)
    odd_file = fake.add_file(
        parent=folder,
        attachment_id=None,
        created=OLD,
        props={"mm_service": "org-admin-api", "kind": "attachment"},
    )
    result = await _service(store, Checker({referenced})).run(apply=True, max_deletes=10)
    assert result.deleted == 1 and result.skipped == 1 and result.scanned == 4
    assert orphan_file not in fake.files
    assert {ref_file, young_file, odd_file} <= set(fake.files)


async def test_apply_refuses_without_checker(fake: FakeDrive, store: GoogleDriveStore) -> None:
    folder = fake.add_folder("ricotdin")
    fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=OLD)
    with pytest.raises(SweepRefused) as exc:
        await _service(store, None).run(apply=True, max_deletes=10)
    assert exc.value.reason == "no_reference_checker"
    assert fake.count("delete") == 0
    result = await _service(store, None).run(apply=False, max_deletes=10)
    assert len(result.candidates) == 1 and result.references_checked is False


async def test_checker_error_aborts_without_deletes(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    folder = fake.add_folder("ricotdin")
    fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=OLD)
    with pytest.raises(SweepRefused) as exc:
        await _service(store, Checker(set(), fail=True)).run(apply=True, max_deletes=10)
    assert exc.value.reason == "reference_check_failed"
    assert exc.value.__cause__ is None
    assert fake.count("delete") == 0


async def test_max_deletes_aborts_before_deleting(fake: FakeDrive, store: GoogleDriveStore) -> None:
    folder = fake.add_folder("ricotdin")
    for _ in range(3):
        fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=OLD)
    with pytest.raises(SweepRefused) as exc:
        await _service(store, Checker(set())).run(apply=True, max_deletes=2)
    assert exc.value.reason == "too_many"
    assert fake.count("delete") == 0


async def test_grace_floor(store: GoogleDriveStore) -> None:
    with pytest.raises(ValueError):
        _service(store, Checker(set()), hours=23)
    _service(store, Checker(set()), hours=24)


async def test_delete_error_stops_and_reports_counts(
    fake: FakeDrive, store: GoogleDriveStore
) -> None:
    folder = fake.add_folder("ricotdin")
    for _ in range(3):
        fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=OLD)
    fake.queue("delete", 204, 400)
    result = await _service(store, Checker(set())).run(apply=True, max_deletes=10)
    assert result.deleted == 1
    assert result.error_code == DriveErrorCode.REJECTED
    assert fake.count("delete") == 2
