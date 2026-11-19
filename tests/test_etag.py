import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.etag import IfMatch, if_none_match, parse_if_match, strong_etag, version_etag

TAG = strong_etag(b"{}")


def test_strong_etag_format() -> None:
    body = b'{"app":"stoktok","roles":[]}'
    tag = strong_etag(body)
    assert tag == '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    inner = tag[1:-1]
    assert len(inner) == 32 and inner == inner.lower()
    int(inner, 16)


def test_if_none_match_exact() -> None:
    assert if_none_match([TAG], TAG)


def test_if_none_match_weak_prefix() -> None:
    assert if_none_match([f"W/{TAG}"], TAG)


@pytest.mark.parametrize(
    "values",
    [
        [f'"other", {TAG}'],
        [f'"a",{TAG} , "b"'],
        ['"other"', TAG],  # several headers are joined
        [f'W/"x", W/{TAG}'],
    ],
)
def test_if_none_match_list(values: list[str]) -> None:
    assert if_none_match(values, TAG)


@pytest.mark.parametrize("values", [["*"], [" * "]])
def test_if_none_match_star(values: list[str]) -> None:
    assert if_none_match(values, TAG)


@pytest.mark.parametrize("values", [[], [""], ['"other"'], ['"a", "b"']])
def test_if_none_match_no_match(values: list[str]) -> None:
    assert not if_none_match(values, TAG)


@pytest.mark.parametrize(
    "values",
    [
        [TAG[1:-1]],  # unquoted
        [f"W/{TAG[1:-1]}"],  # W/ without quotes
        [f'"{TAG[1:-1]}"x"'],  # embedded quote
        ['"'],
        [f"*, {TAG[:-1]}"],  # `*` inside a list never matches; truncated tag
        ["*, " + '"x"'],
        [f"{TAG}, " + '"x", ' * 500],  # over 2048 chars
    ],
)
def test_if_none_match_malformed(values: list[str]) -> None:
    assert not if_none_match(values, TAG)


def test_version_etag_is_quoted_epoch_microseconds() -> None:
    dt = datetime(2025, 11, 19, 7, 0, 0, 123456, tzinfo=UTC)
    assert version_etag(dt) == '"1763535600123456"'
    assert version_etag(dt.astimezone(timezone(timedelta(hours=7)))) == version_etag(dt)


@pytest.mark.parametrize("values", [None, [], [""], ["  "]])
def test_parse_if_match_absent(values: list[str] | None) -> None:
    assert parse_if_match(values) is None


@pytest.mark.parametrize("values", [["*"], [" * "]])
def test_parse_if_match_star(values: list[str]) -> None:
    assert parse_if_match(values) == IfMatch(any=True)


def test_parse_if_match_round_trip() -> None:
    dt = datetime(2025, 11, 19, 7, 0, 0, 123456, tzinfo=UTC)
    parsed = parse_if_match([version_etag(dt)])
    assert parsed is not None and parsed.versions == {dt}


@pytest.mark.parametrize("values", [['"1", "2"'], ['"1"', '"2"']])
def test_parse_if_match_list_and_multiple_lines(values: list[str]) -> None:
    parsed = parse_if_match(values)
    assert parsed is not None and len(parsed.versions) == 2


@pytest.mark.parametrize(
    "value",
    ['W/"1"', '"abc"', "1", '*, "1"', '"999999999999999999"', '""', '"' + "1" * 2048 + '"'],
)
def test_parse_if_match_never_matches(value: str) -> None:
    assert parse_if_match([value]) == IfMatch(any=False, versions=frozenset())
