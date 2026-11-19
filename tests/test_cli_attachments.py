"""`attachments-sweep` and `attachments-init-root` CLI (ADM-027): fixed output, no Drive ids."""

import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from app.cli import (
    ATTACHMENTS_SWEEP_USAGE,
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_USAGE,
    MSG_ATTACHMENTS_NOT_CONFIGURED,
    MSG_NO_TTY,
    amain,
)
from app.core.config import Settings
from app.modules.attachments import drive as drive_mod
from tests.fake_drive import (
    CLIENT_ID,
    CLIENT_SECRET,
    GOOGLE_TEXT,
    REFRESH_TOKEN,
    ROOT_ID,
    FakeDrive,
    drive_settings,
)

OLD = datetime.now(UTC) - timedelta(hours=100)
YOUNG = datetime.now(UTC) - timedelta(hours=1)


class Checker:
    def __init__(self, referenced: set[uuid.UUID]) -> None:
        self._referenced = referenced

    async def referenced(self, attachment_ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
        return self._referenced & set(attachment_ids)


class FakeTty:
    def __init__(
        self, *, isatty: bool = True, lines: Sequence[str] = (), secrets: Sequence[str] = ()
    ) -> None:
        self._isatty = isatty
        self.lines = list(lines)
        self.secrets = list(secrets)

    def isatty(self) -> bool:
        return self._isatty

    def read_line(self, prompt: str) -> str:
        return self.lines.pop(0)

    def read_secret(self, prompt: str) -> str:
        return self.secrets.pop(0)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drive_mod, "_BACKOFF_BASE_SECONDS", 0.0)


@pytest.fixture
def fake() -> FakeDrive:
    return FakeDrive()


def _seed(fake: FakeDrive) -> tuple[uuid.UUID, uuid.UUID, str, str]:
    folder = fake.add_folder("ricotdin")
    orphan, referenced = uuid.uuid4(), uuid.uuid4()
    o = fake.add_file(parent=folder, attachment_id=orphan, created=OLD)
    fake.add_file(parent=folder, attachment_id=referenced, created=OLD)
    fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=YOUNG)
    return orphan, referenced, o, folder


def _assert_no_ids(fake: FakeDrive, text: str) -> None:
    for secret in [*fake.files, ROOT_ID, CLIENT_SECRET, REFRESH_TOKEN, GOOGLE_TEXT]:
        assert secret not in text


async def _run(
    argv: list[str], fake: FakeDrive, checker: Checker | None, settings: Settings | None = None
) -> int:
    return await amain(
        argv,
        settings=settings or drive_settings(),
        reference_checker=checker,
        drive_transport=fake.transport,
    )


async def test_sweep_dry_run_lists_and_deletes_nothing(
    fake: FakeDrive, capsys: pytest.CaptureFixture[str]
) -> None:
    orphan, referenced, _, _ = _seed(fake)
    assert await _run(["attachments-sweep"], fake, Checker({referenced})) == EXIT_OK
    out, err = capsys.readouterr()
    assert f"would delete attachment {orphan} app ricotdin age 100 h" in out
    assert str(referenced) not in out
    assert "scanned 3, skipped 0 (no ids), would delete 1 (older than 48 h)" in out
    assert out.rstrip().endswith("dry-run: nothing was deleted")
    assert fake.count("delete") == 0 and err == ""


async def test_sweep_apply_deletes_only_old_unreferenced(
    fake: FakeDrive, capsys: pytest.CaptureFixture[str]
) -> None:
    orphan, referenced, orphan_file, _ = _seed(fake)
    before = len(fake.files)
    assert await _run(["attachments-sweep", "--apply"], fake, Checker({referenced})) == EXIT_OK
    out, _ = capsys.readouterr()
    assert f"deleted attachment {orphan} app ricotdin age 100 h" in out
    assert "deleted 1 (older than 48 h)" in out
    assert orphan_file not in fake.files and len(fake.files) == before - 1


async def test_sweep_apply_refuses_without_reference_checker(
    fake: FakeDrive, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake)
    assert await _run(["attachments-sweep", "--apply"], fake, None) == EXIT_FAILURE
    _, err = capsys.readouterr()
    assert "--apply refused" in err
    assert fake.count("delete") == 0
    # A dry run without a checker works and says so.
    assert await _run(["attachments-sweep"], fake, None) == EXIT_OK
    assert "references not checked" in capsys.readouterr().out


async def test_sweep_not_configured(fake: FakeDrive, capsys: pytest.CaptureFixture[str]) -> None:
    code = await _run(["attachments-sweep"], fake, None, Settings(_env_file=None))  # type: ignore[call-arg]
    assert code == EXIT_FAILURE
    assert capsys.readouterr().err.strip() == MSG_ATTACHMENTS_NOT_CONFIGURED
    assert fake.requests == []


@pytest.mark.parametrize(
    "extra",
    [
        ["--grace-hours", "23"],
        ["--grace-hours", "abc"],
        ["--grace-hours", "٣٠"],
        ["--grace-hours", "-1"],
        ["--grace-hours", "8761"],
        ["--max-deletes", "0"],
        ["--max-deletes", "10001"],
        ["--bogus"],
    ],
)
async def test_sweep_grace_floor_and_usage(
    fake: FakeDrive, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    assert await _run(["attachments-sweep", *extra], fake, Checker(set())) == EXIT_USAGE
    assert capsys.readouterr().err.strip() == ATTACHMENTS_SWEEP_USAGE
    assert fake.requests == []


async def test_sweep_grace_hours_accepted(
    fake: FakeDrive, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake)
    assert await _run(["attachments-sweep", "--grace-hours", "24"], fake, Checker(set())) == EXIT_OK
    assert "would delete 2 (older than 24 h)" in capsys.readouterr().out


async def test_sweep_max_deletes_aborts(
    fake: FakeDrive, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = fake.add_folder("ricotdin")
    for _ in range(2):
        fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=OLD)
    argv = ["attachments-sweep", "--apply", "--max-deletes", "1"]
    assert await _run(argv, fake, Checker(set())) == EXIT_FAILURE
    assert "more candidates than --max-deletes" in capsys.readouterr().err
    assert fake.count("delete") == 0


async def test_sweep_output_has_no_drive_ids(
    fake: FakeDrive, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fake)
    await _run(["attachments-sweep"], fake, Checker(set()))
    await _run(["attachments-sweep", "--apply"], fake, Checker(set()))
    fake.queue("delete", 500, 500, 500)
    folder = fake.add_folder("tudo")
    fake.add_file(parent=folder, attachment_id=uuid.uuid4(), created=OLD, app_key="tudo")
    await _run(["attachments-sweep", "--apply"], fake, Checker(set()))
    fake.queue("list", 500, 500, 500)
    await _run(["attachments-sweep"], fake, Checker(set()))
    out, err = capsys.readouterr()
    assert "Drive unavailable" in err
    _assert_no_ids(fake, out + err)


async def test_init_root_creates_folder_and_prints_id(
    fake: FakeDrive, capsys: pytest.CaptureFixture[str]
) -> None:
    tty = FakeTty(lines=[CLIENT_ID], secrets=[CLIENT_SECRET, REFRESH_TOKEN])
    code = await amain(["attachments-init-root"], tty=tty, drive_transport=fake.transport)
    assert code == EXIT_OK
    out, err = capsys.readouterr()
    created = [f for fid, f in fake.files.items() if fid != ROOT_ID]
    assert len(created) == 1 and fake.count("folder") == 1
    assert not created[0].get("parents")
    assert created[0]["appProperties"] == {"mm_service": "org-admin-api", "kind": "root"}
    new_id = next(fid for fid in fake.files if fid != ROOT_ID)
    assert out.count(new_id) == 1
    assert CLIENT_SECRET not in out + err and REFRESH_TOKEN not in out + err


async def test_init_root_no_tty_and_drive_error(
    fake: FakeDrive, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await amain(
        ["attachments-init-root"], tty=FakeTty(isatty=False), drive_transport=fake.transport
    )
    assert code == EXIT_FAILURE
    assert capsys.readouterr().err.strip() == MSG_NO_TTY
    fake.queue("folder", 403)
    tty = FakeTty(lines=[CLIENT_ID], secrets=[CLIENT_SECRET, REFRESH_TOKEN])
    code = await amain(["attachments-init-root"], tty=tty, drive_transport=fake.transport)
    assert code == EXIT_FAILURE
    out, err = capsys.readouterr()
    assert "Drive rejected" in err
    assert GOOGLE_TEXT not in out + err and CLIENT_SECRET not in out + err
