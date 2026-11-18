import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.dialects import postgresql

from app.core.listing import SearchTerm, escape_like, ilike_any, sort_clauses
from app.modules.organizations.models import Organization


def _sql(clause: object) -> str:
    return str(clause.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


def test_escape_like_escapes_backslash_first() -> None:
    assert escape_like("%") == "\\%"
    assert escape_like("_") == "\\_"
    assert escape_like("\\") == "\\\\"
    assert escape_like("\\%") == "\\\\\\%"


def test_ilike_any_ors_columns_with_escape() -> None:
    clause = ilike_any("a_b", Organization.name, Organization.slug)
    compiled = clause.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.count("ILIKE") == 2
    assert sql.count("ESCAPE '\\'") == 2
    assert " OR " in sql
    assert list(compiled.params.values()) == ["%a\\_b%", "%a\\_b%"]


def test_ilike_any_needs_a_column() -> None:
    with pytest.raises(ValueError):
        ilike_any("x")


def test_search_term_strips_and_normalizes() -> None:
    adapter = TypeAdapter(SearchTerm)
    assert adapter.validate_python("  acme ") == "acme"
    assert adapter.validate_python("é") == "é"


def test_search_term_rejects_empty_whitespace_and_too_long() -> None:
    adapter = TypeAdapter(SearchTerm)
    for bad in ["", "   ", "x" * 101]:
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
    assert adapter.validate_python("x" * 100) == "x" * 100
    assert adapter.validate_python("  " + "x" * 100 + "  ") == "x" * 100


def test_sort_clauses_asc_desc_and_tiebreaker() -> None:
    cols = {"name": Organization.name}
    asc = sort_clauses("name", cols, Organization.id)
    desc = sort_clauses("-name", cols, Organization.id)
    assert [_sql(c) for c in asc] == ["organizations.name ASC", "organizations.id ASC"]
    assert [_sql(c) for c in desc] == ["organizations.name DESC", "organizations.id ASC"]


def test_sort_clauses_unknown_key_raises() -> None:
    for bad in ["bogus", "-bogus"]:
        with pytest.raises(ValueError):
            sort_clauses(bad, {"name": Organization.name}, Organization.id)
