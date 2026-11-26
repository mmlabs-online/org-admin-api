"""Display text building blocks (ADR-0003 §11): NFC, then length, then a Unicode category check."""

import unicodedata
from collections.abc import Callable
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator
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


def trimmed(max_length: int) -> Callable[[str], str]:
    """Strip, then 1..max_length (pydantic checks `min_length` before it strips)."""

    def check(value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= max_length:
            raise PydanticCustomError("display_text", f"must be 1-{max_length} characters")
        return value

    return check


def collapse_spaces(value: str) -> str:
    """After-validator: every run of `Zs` characters becomes one U+0020 (Cc is left to the
    category check)."""
    out: list[str] = []
    in_run = False
    for ch in value:
        if unicodedata.category(ch) == "Zs":
            if not in_run:
                out.append(" ")
            in_run = True
        else:
            out.append(ch)
            in_run = False
    return "".join(out)


# Characters that render as nothing but are letters or symbols (Hangul fillers, braille blank).
BLANK_LOOKALIKES = frozenset({"\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"})
MAX_COMBINING_RUN = 2


def reject_blank_lookalikes(value: str) -> str:
    if any(ch in BLANK_LOOKALIKES for ch in value):
        raise PydanticCustomError("display_text", "contains a disallowed character")
    return value


def cap_combining_marks(value: str) -> str:
    """After-validator: at most `MAX_COMBINING_RUN` consecutive `Mn`/`Me` code points."""
    run = 0
    for ch in value:
        run = run + 1 if unicodedata.category(ch) in {"Mn", "Me"} else 0
        if run > MAX_COMBINING_RUN:
            raise PydanticCustomError("display_text", "contains too many combining marks")
    return value


DisplayName = Annotated[
    str,
    BeforeValidator(nfc_if_str),
    AfterValidator(trimmed(100)),
    AfterValidator(reject_disallowed_categories),
]
# A person's display name (USR-33, D40): stricter than `DisplayName`.
UserDisplayName = Annotated[
    str,
    BeforeValidator(nfc_if_str),
    AfterValidator(lambda v: v.strip()),
    AfterValidator(collapse_spaces),
    AfterValidator(trimmed(100)),
    AfterValidator(reject_disallowed_categories),
    AfterValidator(reject_blank_lookalikes),
    AfterValidator(cap_combining_marks),
]


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
