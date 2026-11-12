"""Per-process cache of the browser origins of active apps (`OriginRegistry`, ADR-0006).

TTL 60 s, single-flight refresh, fail closed (a load error serves an empty set for 5 s).
App writes set `session.info[APPS_CHANGED_KEY]` (repository); the `after_commit` listener below
then invalidates every registry in this process. A rollback drops the flag. Other processes
(the CLI, manual SQL) rely on the TTL.
"""

import asyncio
import logging
import time
import weakref
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.modules.rbac.origins import allowed_origins
from app.modules.rbac.repository import APPS_CHANGED_KEY, RbacRepository

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_registries: "weakref.WeakSet[AppOriginRegistry]" = weakref.WeakSet()


class AppOriginRegistry:
    def __init__(
        self,
        session_factory: SessionFactory,
        app_env: str,
        *,
        ttl_seconds: float = 60.0,
        failure_backoff_seconds: float = 5.0,
        load_timeout_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session_factory = session_factory
        self.app_env = app_env
        self.ttl_seconds = ttl_seconds
        self.failure_backoff_seconds = failure_backoff_seconds
        self.load_timeout_seconds = load_timeout_seconds
        self.clock = clock
        self._origins: frozenset[str] = frozenset()
        self._expires_at = 0.0
        self._generation = 0
        self._lock = asyncio.Lock()
        _registries.add(self)

    async def is_allowed(self, origin: str) -> bool:
        if self.clock() >= self._expires_at:
            await self._refresh()
        return origin in self._origins

    def invalidate(self) -> None:
        self._generation += 1
        self._expires_at = 0.0

    async def _refresh(self) -> None:
        async with self._lock:
            if self.clock() < self._expires_at:
                return  # another waiter refreshed
            generation = self._generation
            try:
                async with asyncio.timeout(self.load_timeout_seconds):
                    async with self.session_factory() as session:
                        rows = await RbacRepository(session).active_app_origins()
            except Exception as exc:  # fail closed; class name only (R5/R11)
                self._origins = frozenset()
                self._expires_at = self.clock() + self.failure_backoff_seconds
                logger.warning("cors_origins_refresh_failed error=%s", type(exc).__name__)
                return
            self._origins = frozenset(o for row in rows for o in allowed_origins(row, self.app_env))
            # Invalidated during the load: serve the result once, reload on the next call.
            fresh = generation == self._generation
            self._expires_at = self.clock() + self.ttl_seconds if fresh else 0.0


def invalidate_all() -> None:
    for registry in list(_registries):
        registry.invalidate()


def _on_commit(session: Session) -> None:
    if session.info.pop(APPS_CHANGED_KEY, False):
        invalidate_all()


def _on_soft_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    if previous_transaction.parent is None:
        session.info.pop(APPS_CHANGED_KEY, None)


event.listen(Session, "after_commit", _on_commit)
event.listen(Session, "after_soft_rollback", _on_soft_rollback)
