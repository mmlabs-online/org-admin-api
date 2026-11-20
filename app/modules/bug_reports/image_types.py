"""Image type checks for bug-report attachments (ADM-030, ADR-0023). Pure.

The declared `Content-Type` is only a hint: the stored type is what the magic bytes say, and the
two must agree. The API never re-encodes an image.
"""

from app.modules.attachments.store import ImageType

MIN_IMAGE_BYTES = 12  # the longest signature checked (WebP: RIFF + size + WEBP)
_PNG = b"\x89PNG\r\n\x1a\n"
_EXTENSIONS = {
    ImageType.PNG: "png",
    ImageType.JPEG: "jpg",
    ImageType.WEBP: "webp",
    ImageType.GIF: "gif",
}


def parse_declared(header: str | None) -> ImageType | None:
    """The media type of a `Content-Type` header, parameters stripped, case-insensitive."""
    if not header:
        return None
    media = header.split(";", 1)[0].strip().lower()
    try:
        return ImageType(media)
    except ValueError:
        return None


def sniff(head: bytes) -> ImageType | None:
    """The image type the leading bytes say, or None."""
    if head.startswith(_PNG):
        return ImageType.PNG
    if head.startswith(b"\xff\xd8\xff"):
        return ImageType.JPEG
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ImageType.GIF
    if len(head) >= MIN_IMAGE_BYTES and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ImageType.WEBP
    return None


def extension(image_type: ImageType) -> str:
    return _EXTENSIONS[image_type]
