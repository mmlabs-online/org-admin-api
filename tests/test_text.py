"""Display text validation (ADM-011, ADR-0003 §11)."""

import unicodedata

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

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
