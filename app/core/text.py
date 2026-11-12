"""Display text building blocks (ADR-0003 §11): NFC, then length, then a Unicode category check."""

import unicodedata

from pydantic_core import PydanticCustomError

# Controls, format chars (bidi, zero-width, BOM, tags), line/paragraph separators, private use,
# surrogates and unassigned code points (Python's Unicode database).
DISALLOWED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"})


def nfc_if_str(value: object) -> object:
    """Before-validator: NFC-normalize strings so length limits and storage see the NFC form."""
    return unicodedata.normalize("NFC", value) if isinstance(value, str) else value


def reject_disallowed_categories(value: str) -> str:
    """After-validator: refuse any disallowed code point; the error never echoes the value."""
    if any(unicodedata.category(ch) in DISALLOWED_CATEGORIES for ch in value):
        raise PydanticCustomError("display_text", "contains a disallowed character")
    return value


def mask_email(email: str) -> str:
    """Mask an email for operator output only (R11): `john.doe@example.com` -> `j***@e***.com`.

    The mask is always `***`, so the length of the hidden parts is not leaked.
    """
    local, sep, domain = email.rpartition("@")
    if not sep:
        return "***"
    labels = domain.split(".")
    masked_domain = labels[0][:1] + "***"
    if len(labels) >= 2:
        masked_domain += "." + labels[-1]
    return f"{local[:1]}***@{masked_domain}"
