"""`read_exact`: byte cap, exact length and deadline of an upload body (ADM-030)."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.core.errors import PayloadTooLargeError
from app.modules.bug_reports.errors import AttachmentIncompleteError
from app.modules.bug_reports.upload import read_exact


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_exact_length_ok() -> None:
    data = await read_exact(_chunks(b"ab", b"", b"cde"), expected=5, cap=10, deadline_s=5)
    assert data == b"abcde"


async def test_short_body_incomplete() -> None:
    with pytest.raises(AttachmentIncompleteError):
        await read_exact(_chunks(b"abc"), expected=5, cap=10, deadline_s=5)


async def test_over_expected_is_too_large() -> None:
    with pytest.raises(PayloadTooLargeError):
        await read_exact(_chunks(b"abc", b"defg"), expected=5, cap=10, deadline_s=5)


async def test_over_cap_is_too_large() -> None:
    with pytest.raises(PayloadTooLargeError):
        await read_exact(_chunks(b"a" * 8), expected=8, cap=4, deadline_s=5)


async def test_deadline_incomplete() -> None:
    async def stalled() -> AsyncIterator[bytes]:
        yield b"ab"
        await asyncio.sleep(30)
        yield b"cde"

    with pytest.raises(AttachmentIncompleteError):
        await asyncio.wait_for(read_exact(stalled(), expected=5, cap=10, deadline_s=0.1), 10)
