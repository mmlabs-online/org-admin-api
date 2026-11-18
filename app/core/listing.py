"""Shared list primitives: LIKE search, search-term type and allow-listed sort (ADM-020)."""

from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BeforeValidator, StringConstraints
from sqlalchemy import ColumnElement, or_
from sqlalchemy.orm import QueryableAttribute

from app.core.text import nfc_if_str

SEARCH_TERM_MAX_LENGTH = 100


def _nfc_strip(value: object) -> object:
    normalized = nfc_if_str(value)
    return normalized.strip() if isinstance(normalized, str) else normalized


# NFC, then strip, then 1..100 chars: an empty or whitespace-only term is a validation error.
# Strip runs in the before-validator because the length constraints would otherwise see the
# untrimmed value.
SearchTerm = Annotated[
    str,
    BeforeValidator(_nfc_strip),
    StringConstraints(min_length=1, max_length=SEARCH_TERM_MAX_LENGTH),
]


def escape_like(term: str) -> str:
    """Escape LIKE wildcards so `term` matches literally (backslash first)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ilike_any(
    term: str, *columns: ColumnElement[Any] | QueryableAttribute[Any]
) -> ColumnElement[bool]:
    """Case-insensitive literal substring match of `term` on any of `columns`."""
    if not columns:
        raise ValueError("ilike_any needs at least one column")
    pattern = f"%{escape_like(term)}%"
    return or_(*(c.ilike(pattern, escape="\\") for c in columns))


def sort_clauses(
    sort: str,
    columns: Mapping[str, QueryableAttribute[Any]],
    tiebreaker: QueryableAttribute[Any],
) -> list[ColumnElement[Any]]:
    """ORDER BY clauses for `sort` (`key` or `-key`); `columns` is the allow-list.

    The tie-breaker is always ascending, in both directions.
    """
    key = sort.removeprefix("-")
    column = columns.get(key)
    if column is None:
        raise ValueError(f"unsupported sort: {sort!r}")
    primary = column.desc() if sort.startswith("-") else column.asc()
    return [primary, tiebreaker.asc()]
