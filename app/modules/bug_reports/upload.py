"""Bounded read of an upload body (ADM-030, ADR-0023)."""

import asyncio
from collections.abc import AsyncIterator

from app.core.errors import PayloadTooLargeError
from app.modules.bug_reports.errors import AttachmentIncompleteError


async def read_exact(
    chunks: AsyncIterator[bytes], *, expected: int, cap: int, deadline_s: float
) -> bytes:
    """Collect exactly `expected` bytes (the declared `Content-Length`) within `deadline_s`.

    More than `expected` or `cap` bytes is 413 as soon as it is seen; fewer, or a stall past the
    deadline, is 422 `attachment_incomplete`."""
    buf = bytearray()
    timed_out = False

    async def collect() -> None:
        async for chunk in chunks:
            buf.extend(chunk)
            if len(buf) > expected or len(buf) > cap:
                raise PayloadTooLargeError("Attachment is too large")

    try:
        async with asyncio.timeout(deadline_s):
            await collect()
    except TimeoutError:
        timed_out = True
    if timed_out or len(buf) != expected:
        raise AttachmentIncompleteError("Attachment body is incomplete")
    return bytes(buf)
