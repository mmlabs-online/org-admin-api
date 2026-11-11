import hashlib

import pytest

from app.core.etag import if_none_match, strong_etag

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
