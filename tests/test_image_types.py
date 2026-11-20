"""Pure image type checks (ADM-030)."""

import pytest

from app.modules.attachments.store import ImageType
from app.modules.bug_reports import image_types

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8
GIF = b"GIF89a" + b"\x00" * 8
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("image/png", ImageType.PNG),
        ("image/jpeg", ImageType.JPEG),
        ("image/webp", ImageType.WEBP),
        ("image/gif", ImageType.GIF),
        ("image/PNG", ImageType.PNG),
        ("image/png; charset=x", ImageType.PNG),
        ("  image/gif  ", ImageType.GIF),
        ("image/jpg", None),
        ("image/svg+xml", None),
        ("text/html", None),
        ("application/pdf", None),
        ("application/octet-stream", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_declared(header: str | None, expected: ImageType | None) -> None:
    assert image_types.parse_declared(header) == expected


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (PNG, ImageType.PNG),
        (JPEG, ImageType.JPEG),
        (b"GIF87a" + b"\x00" * 8, ImageType.GIF),
        (GIF, ImageType.GIF),
        (WEBP, ImageType.WEBP),
        (b"GIF88a" + b"\x00" * 8, None),
        (b"RIFF\x24\x00\x00\x00WAVEfmt ", None),
        (b"RIFF\x24\x00\x00\x00WEBP"[:11], None),
        (PNG[:6], None),
        (b"\xff\xd8", None),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None),
        (b"<!DOCTYPE html><html>", None),
        (b"%PDF-1.7 pdf pdf pdf", None),
        (b"", None),
    ],
)
def test_sniff(head: bytes, expected: ImageType | None) -> None:
    assert image_types.sniff(head[: image_types.MIN_IMAGE_BYTES]) == expected


def test_polyglot_png_with_html_passes_sniff() -> None:
    """Known residual risk (ADR-0023): a valid PNG header followed by HTML is accepted. The
    download headers (nosniff, CSP sandbox, attachment) are what contain it."""
    assert image_types.sniff(PNG + b"<script>alert(1)</script>") == ImageType.PNG


def test_extension() -> None:
    assert [image_types.extension(t) for t in ImageType] == ["png", "jpg", "webp", "gif"]
