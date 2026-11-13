"""In-process email queue: sends after the request, with retry, backoff and a circuit breaker.

The raw token lives only in the queued message (memory); a restart loses queued mail and the
user presses resend (ADR-0007). Logs carry the event, purpose, user id, attempt and SMTP code
only: never the address, link, token or exception text (R11).
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Sequence

from app.modules.email.sender import EmailDeliveryError, EmailSender, OutgoingEmail

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 8.0)  # before attempts 2 and 3, +/- 20 % jitter
PAUSE_SECONDS = 3600.0  # after a quota or authentication failure
QUEUE_MAXSIZE = 1000


class EmailDispatcher:
    def __init__(
        self,
        sender: EmailSender,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        backoff: Sequence[float] = BACKOFF_SECONDS,
        pause_seconds: float = PAUSE_SECONDS,
        maxsize: int = QUEUE_MAXSIZE,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sender = sender
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._pause_seconds = pause_seconds
        self._sleep = sleep
        self._clock = clock
        self._queue: asyncio.Queue[OutgoingEmail] = asyncio.Queue(maxsize=maxsize)
        self._worker: asyncio.Task[None] | None = None  # strong reference
        self._paused_until = 0.0

    async def enqueue(self, email: OutgoingEmail) -> None:
        """Queue without waiting (async: an `asyncio.Queue` is not thread-safe)."""
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run())
        try:
            self._queue.put_nowait(email)
        except asyncio.QueueFull:
            _log(logging.WARNING, "email_dropped reason=queue_full", email)

    async def join(self) -> None:
        """Wait until every queued message was handled (tests)."""
        await self._queue.join()

    async def aclose(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    async def _run(self) -> None:
        while True:
            email = await self._queue.get()
            try:
                await self._deliver(email)
            except Exception as exc:  # the worker never dies
                event = f"email_failed reason=unexpected error={type(exc).__name__}"
                _log(logging.ERROR, event, email)
            finally:
                self._queue.task_done()

    async def _deliver(self, email: OutgoingEmail) -> None:
        for attempt in range(1, self._max_attempts + 1):
            if self._clock() < self._paused_until:
                _log(logging.WARNING, "email_dropped reason=paused", email)
                return
            try:
                await self._sender.send(email)
            except EmailDeliveryError as exc:
                extra = f"attempt={attempt} smtp_code={exc.smtp_code}"
                if exc.kind in {"quota", "auth"}:
                    self._paused_until = self._clock() + self._pause_seconds
                    event = "email_quota_exceeded" if exc.kind == "quota" else "email_auth_failed"
                    _log(logging.WARNING, f"{event} {extra}", email)
                    return
                if exc.kind == "permanent" or attempt == self._max_attempts:
                    _log(logging.ERROR, f"email_failed kind={exc.kind} {extra}", email)
                    return
                _log(logging.INFO, f"email_retry {extra}", email)
                delay = self._backoff[min(attempt, len(self._backoff)) - 1]
                await self._sleep(delay * random.uniform(0.8, 1.2))  # noqa: S311 (jitter)
                continue
            _log(logging.INFO, f"email_sent attempt={attempt}", email)
            return


def _log(level: int, event: str, email: OutgoingEmail) -> None:
    logger.log(level, "%s purpose=%s user_id=%s", event, email.purpose, email.user_id)
