"""Password normalisation, policy and Argon2id hashing (R11)."""

import gzip
import unicodedata
from functools import cache
from pathlib import Path

import anyio
import anyio.to_thread
from argon2 import PasswordHasher as Argon2Hasher
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.modules.auth.errors import WeakPasswordError

_COMMON_PASSWORDS_FILE = Path(__file__).parent / "data" / "common-passwords.txt.gz"


def normalize_password(password: str) -> str:
    """NFKC-normalise (no trimming). Use for both hashing and verification."""
    return unicodedata.normalize("NFKC", password)


@cache
def common_passwords() -> frozenset[str]:
    """Bundled SecLists list (lower-cased, length >= 8), loaded once."""
    with gzip.open(_COMMON_PASSWORDS_FILE, "rt", encoding="utf-8") as f:
        return frozenset(line.rstrip("\n") for line in f if line.strip())


class PasswordPolicy:
    def __init__(self, min_length: int) -> None:
        self.min_length = min_length

    def check(self, password: str) -> None:
        normalized = normalize_password(password)
        reasons: list[str] = []
        if len(normalized) < self.min_length:
            reasons.append("too_short")
        if normalized.lower() in common_passwords():
            reasons.append("too_common")
        if reasons:
            raise WeakPasswordError(
                "Password does not meet the policy", details={"reasons": reasons}
            )


class PasswordHasher:
    """Argon2id; hashing/verification run in worker threads behind a concurrency cap."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = Argon2Hasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
            type=Type.ID,
        )
        self._max_concurrency = settings.argon2_max_concurrency
        self._limiter: anyio.CapacityLimiter | None = None

    def _capacity(self) -> anyio.CapacityLimiter:
        if self._limiter is None:  # created lazily inside the running event loop
            self._limiter = anyio.CapacityLimiter(self._max_concurrency)
        return self._limiter

    async def hash(self, password: str) -> str:
        return await anyio.to_thread.run_sync(
            self._hasher.hash, normalize_password(password), limiter=self._capacity()
        )

    async def verify(self, password_hash: str, password: str) -> bool:
        def _verify() -> bool:
            try:
                return self._hasher.verify(password_hash, normalize_password(password))
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False

        return await anyio.to_thread.run_sync(_verify, limiter=self._capacity())

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)
