"""Sweep of untracked Drive files (ADM-027, ADR-0018). Fails closed: nothing is deleted unless
every safety rail holds. Permanent deletes only touch files this service created (marker in
`appProperties`, listed under its own root) that are older than the grace period and that no
attachment row references."""

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.modules.attachments.errors import DriveError, DriveErrorCode
from app.modules.attachments.store import AttachmentStore

MIN_GRACE = timedelta(hours=24)
DEFAULT_GRACE_HOURS = 48
DEFAULT_MAX_DELETES = 100


class ReferenceChecker(Protocol):
    async def referenced(self, attachment_ids: Collection[UUID]) -> set[UUID]: ...


class SweepRefused(Exception):
    """`reason` is one of: no_reference_checker, reference_check_failed, too_many."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SweepItem:
    attachment_id: UUID
    app_key: str | None
    age_hours: int


@dataclass
class SweepResult:
    scanned: int = 0
    skipped: int = 0
    candidates: list[SweepItem] = field(default_factory=list)
    deleted: int = 0
    references_checked: bool = False
    error_code: DriveErrorCode | None = None  # never a file id


class SweepService:
    def __init__(
        self,
        store: AttachmentStore,
        checker: ReferenceChecker | None,
        *,
        grace: timedelta,
        now: Callable[[], datetime],
    ) -> None:
        if grace < MIN_GRACE:
            raise ValueError("grace is below the 24 h floor")
        self._store = store
        self._checker = checker
        self._grace = grace
        self._now = now

    async def run(self, *, apply: bool, max_deletes: int) -> SweepResult:
        if apply and self._checker is None:
            raise SweepRefused("no_reference_checker")
        result = SweepResult(references_checked=self._checker is not None)
        now = self._now()
        old: dict[UUID, tuple[str, SweepItem]] = {}
        async for stored in self._store.list_files():
            result.scanned += 1
            if stored.attachment_id is None:
                result.skipped += 1
                continue
            age = now - stored.created_at
            if age < self._grace:
                continue
            old[stored.attachment_id] = (
                stored.file_id,
                SweepItem(stored.attachment_id, stored.app_key, int(age.total_seconds() // 3600)),
            )
        if self._checker is not None and old:
            try:
                kept = await self._checker.referenced(list(old))
            except Exception:
                raise SweepRefused("reference_check_failed") from None
            for attachment_id in kept:
                old.pop(attachment_id, None)
        result.candidates = [item for _, item in old.values()]
        if not apply:
            return result
        if len(old) > max_deletes:
            raise SweepRefused("too_many")
        for file_id, _ in old.values():
            try:
                await self._store.delete(file_id)
            except DriveError as exc:
                result.error_code = exc.code
                break
            result.deleted += 1
        return result
