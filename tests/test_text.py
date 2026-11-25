"""Display text validation (ADM-011, ADR-0003 §11)."""

import unicodedata

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.core.text import DisplayName, UserDisplayName, mask_email
from app.modules.rbac.schemas import AppName, Description

APP_NAME = TypeAdapter(AppName)


class _WithDescription(BaseModel):
    description: Description | None = None


@pytest.mark.parametrize(
    "char",
    [
        "\x00",  # Cc
        "\x85",  # Cc (C1)
        "\u00ad",  # Cf soft hyphen
        "\u200d",  # Cf ZWJ
        "\U000e0041",  # Cf tag
        "\u2028",  # Zl
        "\u2029",  # Zp
        "\ue000",  # Co
        "\ud800",  # Cs
        "\u0378",  # Cn
    ],
)
def test_rejects_category(char: str) -> None:
    with pytest.raises(ValidationError) as exc:
        APP_NAME.validate_python(f"Marker{char}Name")
    assert exc.value.errors()[0]["type"] == "display_text"
    with pytest.raises(ValidationError):
        _WithDescription.model_validate({"description": f"x{char}y"})


@pytest.mark.parametrize("value", ["Quản trị viên", "Owner 👑", "Admin ❤️", "a b"])
def test_accepts_display_text(value: str) -> None:
    assert APP_NAME.validate_python(value) == value


def test_nfc_normalized() -> None:
    nfd = unicodedata.normalize("NFD", "Quản")
    assert nfd != "Quản"
    assert APP_NAME.validate_python(nfd) == "Quản"


def test_length_checked_after_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "ả") * 200
    assert len(nfd) == 400
    assert len(APP_NAME.validate_python(nfd)) == 200
    with pytest.raises(ValidationError):
        APP_NAME.validate_python("a" * 256)


def test_error_does_not_echo_value() -> None:
    value = "SecretMarker\u200b"
    with pytest.raises(ValidationError) as exc:
        APP_NAME.validate_python(value)
    error = exc.value.errors()[0]
    assert error["type"] == "display_text"
    assert "SecretMarker" not in error["msg"]
    assert "SecretMarker" not in repr(exc.value.errors(include_input=False))


def test_none_passes_through() -> None:
    assert _WithDescription.model_validate({"description": None}).description is None


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("john.doe@example.com", "j***@e***.com"),
        ("a@b.co", "a***@b***.co"),
        ("x@localhost", "x***@l***"),
        ("john@mail.example.co.uk", "j***@m***.uk"),
        ("\u00e9lodie@ex\u00e4mple.fr", "\u00e9***@e***.fr"),
        ("no-at-sign", "***"),
        ("@example.com", "***@e***.com"),
        ("user@", "u***@***"),
        ("averyveryverylonglocalpart@averyverylongdomain.com", "a***@a***.com"),
    ],
)
def test_mask_email(email: str, masked: str) -> None:
    assert mask_email(email) == masked


USER_NAME = TypeAdapter(UserDisplayName)


@pytest.mark.parametrize(
    "value", ["Ming", "Nguyễn Thị Ánh", "Nguye\u0302\u0303n", "a" * 100, "x\u0300\u0301"]
)
def test_user_display_name_accepts(value: str) -> None:
    assert USER_NAME.validate_python(value) == unicodedata.normalize("NFC", value)


def test_user_display_name_normalises() -> None:
    assert USER_NAME.validate_python("  Ming  \u3000 Le ") == "Ming Le"
    assert USER_NAME.validate_python("Nguye\u0302\u0303n") == "Nguy\u1ec5n"
    assert USER_NAME.validate_python("a" * 98 + "   " + "b") == "a" * 98 + " b"
    with pytest.raises(ValidationError):
        USER_NAME.validate_python("a" * 99 + " b")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "a" * 101,
        "Ada\u202e",
        "Ada\u200b",
        "Ada\u0007",
        "Ada\tB",
        "\u3164",
        "a\u3164b",
        "\u115f",
        "\u1160",
        "\uffa0",
        "\u2800",
        "x\u0300\u0301\u0302",
    ],
)
def test_user_display_name_rejects(value: str) -> None:
    with pytest.raises(ValidationError) as exc:
        USER_NAME.validate_python(value)
    assert exc.value.errors()[0]["type"] == "display_text"
    assert "Ada" not in exc.value.errors()[0]["msg"]


def test_display_name_usage_unchanged() -> None:
    assert TypeAdapter(DisplayName).validate_python("x\u0300\u0301\u0302") == "x\u0300\u0301\u0302"
    assert TypeAdapter(DisplayName).validate_python("a\u00a0b") == "a\u00a0b"
