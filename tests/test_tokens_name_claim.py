import logging
import unicodedata
import uuid

import pytest

from app.modules.tokens.service import _name_claim

UID = uuid.uuid4()


def _claim(value: str | None) -> str | None:
    return _name_claim(value, user_id=UID, aud="stoktok")


def test_name_none_returns_none() -> None:
    assert _claim(None) is None


@pytest.mark.parametrize("value", ["", "   "])
def test_name_empty_or_whitespace_returns_none(
    value: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert _claim(value) is None
    assert "name_out_of_contract" not in caplog.text


@pytest.mark.parametrize("value", ["Ada", "Nguyễn Văn A", "Ada 🙂"])
def test_name_plain_returned_unchanged(value: str) -> None:
    assert _claim(value) == value


def test_name_nfd_input_returned_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "é")
    assert nfd != "é"
    assert _claim(nfd) == "é"


def test_name_exactly_100_ok_101_rejected() -> None:
    assert _claim("a" * 100) == "a" * 100
    assert _claim("a" * 101) is None


@pytest.mark.parametrize("code", [0x202E, 0x200B, 0x0007, 0x2028, 0xE000])
def test_name_disallowed_category_rejected(code: int) -> None:
    assert _claim(f"Ada{chr(code)}") is None


def test_name_rejected_logs_user_id_only(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging.getLogger("app.modules.tokens.service"), "disabled", False)
    with caplog.at_level(logging.DEBUG):
        assert _claim("zznamemarker" + chr(0x202E)) is None
    records = [r.getMessage() for r in caplog.records if "name_out_of_contract" in r.getMessage()]
    assert len(records) == 1
    assert str(UID) in records[0]
    assert "zznamemarker" not in caplog.text
