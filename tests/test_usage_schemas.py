"""ADM-039: request validation of usage metrics (OQ67 reason contract)."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.usage.models import MAX_LIMIT, UsagePeriod
from app.modules.usage.schemas import (
    UsageLimitOverrideRemove,
    UsageLimitOverrideSet,
    UsageMetricCreate,
    UsageMetricUpdate,
)

BASE: dict[str, Any] = {
    "key": "chat-turns",
    "display_name": "Chat turns",
    "unit": "count",
    "default_limit": 10,
    "reason": "start",
}


def _create(**kw: Any) -> UsageMetricCreate:
    return UsageMetricCreate.model_validate({**BASE, **kw})


def test_reason_trimmed_and_required() -> None:
    assert _create(reason="  because  ").reason == "because"
    with pytest.raises(ValidationError):
        UsageMetricCreate.model_validate({k: v for k, v in BASE.items() if k != "reason"})


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_reason_blank_or_501_chars_rejected(reason: str) -> None:
    with pytest.raises(ValidationError):
        _create(reason=reason)
    assert _create(reason="x" * 500)


def test_reason_control_char_rejected() -> None:
    for bad in ("a\x00b", "a‮b", "a\nb"):
        with pytest.raises(ValidationError):
            _create(reason=bad)


@pytest.mark.parametrize("field", ["key", "unit"])
@pytest.mark.parametrize("value", ["", "A", "1a", "a b", "a_b", "x" * 49])
def test_key_and_unit_patterns(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _create(**{field: value})


def test_key_and_unit_accept_slugs() -> None:
    m = _create(key="stt-seconds", unit="s")
    assert (m.key, m.unit) == ("stt-seconds", "s")


@pytest.mark.parametrize("value", [-1, MAX_LIMIT + 1, True, "5", 1.5, None])
def test_default_limit_bounds_and_strict_int(value: Any) -> None:
    with pytest.raises(ValidationError):
        _create(default_limit=value)
    assert _create(default_limit=0).default_limit == 0
    assert _create(default_limit=MAX_LIMIT).default_limit == MAX_LIMIT


def test_period_defaults_to_week() -> None:
    assert _create().period is UsagePeriod.WEEK
    with pytest.raises(ValidationError):
        _create(period="year")


def test_update_requires_a_field_besides_reason() -> None:
    with pytest.raises(ValidationError):
        UsageMetricUpdate.model_validate({"reason": "x"})
    assert UsageMetricUpdate.model_validate({"reason": "x", "default_limit": 0})


def test_update_rejects_null_and_key_unit() -> None:
    for bad in (
        {"default_limit": None},
        {"period": None},
        {"key": "other"},
        {"unit": "seconds"},
    ):
        with pytest.raises(ValidationError):
            UsageMetricUpdate.model_validate({"reason": "x", "display_name": "N", **bad})


def test_override_set_valid() -> None:
    s = UsageLimitOverrideSet.model_validate({"limit": 0, "reason": "x"})
    assert (s.limit, s.expires_at) == (0, None)
    s = UsageLimitOverrideSet.model_validate(
        {"limit": MAX_LIMIT, "expires_at": "2030-01-01T00:00:00Z", "reason": "x"}
    )
    assert s.expires_at is not None and s.expires_at.tzinfo is not None


@pytest.mark.parametrize(
    "change",
    [
        {"limit": True},
        {"limit": 1.5},
        {"limit": "5"},
        {"limit": -1},
        {"limit": MAX_LIMIT + 1},
        {"expires_at": "2030-01-01T00:00:00"},
        {"extra": 1},
        {"reason": ""},
        {"reason": "   "},
        {"reason": None},
    ],
)
def test_override_set_422_cases(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        UsageLimitOverrideSet.model_validate({"limit": 1, "reason": "x", **change})
    with pytest.raises(ValidationError):
        UsageLimitOverrideSet.model_validate({"limit": 1})


def test_override_remove_requires_reason() -> None:
    assert UsageLimitOverrideRemove.model_validate({"reason": "x"})
    for bad in ({}, {"reason": " "}, {"reason": "x", "limit": 1}):
        with pytest.raises(ValidationError):
            UsageLimitOverrideRemove.model_validate(bad)
