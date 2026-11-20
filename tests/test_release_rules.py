"""Pure decision table of the app release write (ADR-0021 §4)."""

from typing import Any

from app.modules.releases.rules import FIELDS, Decision, DecisionKind, StoredDoc, decide_app_write

IMG = [{"name": "ghcr.io/x/y", "digest": "sha256:" + "a" * 64}]
CHG = [{"pr_url": "https://github.com/mmlabs-online/x/pull/1", "title": "t"}]


def _doc(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "git_sha": "a" * 40,
        "deployment_pr": None,
        "has_migrations": None,
        "images": IMG,
        "changes": CHG,
    }
    return {**base, **over}


def _empty(**over: Any) -> dict[str, Any]:
    return _doc(git_sha=None, images=[], changes=[], **over)


def test_none_stored_is_create() -> None:
    assert decide_app_write(None, _doc()) == Decision(DecisionKind.CREATE)


def test_pending_identical_noop() -> None:
    assert decide_app_write(StoredDoc("pending", _doc()), _doc()) == Decision(DecisionKind.NOOP)


def test_pending_diff_replace() -> None:
    got = decide_app_write(StoredDoc("pending", _doc()), _doc(has_migrations=True, changes=[]))
    assert got == Decision(DecisionKind.REPLACE, ("has_migrations", "changes"))


def test_deployed_fill_null_scalars() -> None:
    stored = StoredDoc("deployed", _doc())
    got = decide_app_write(stored, _doc(deployment_pr="https://x", has_migrations=False))
    assert got == Decision(DecisionKind.ENRICH, ("deployment_pr", "has_migrations"))


def test_deployed_fill_empty_lists() -> None:
    got = decide_app_write(StoredDoc("deployed", _empty()), _doc())
    assert got == Decision(DecisionKind.ENRICH, ("git_sha", "images", "changes"))


def test_has_migrations_false_is_non_null() -> None:
    stored = StoredDoc("deployed", _doc(has_migrations=False))
    assert decide_app_write(stored, _doc(has_migrations=True)).kind is DecisionKind.CONFLICT
    assert decide_app_write(stored, _doc(has_migrations=None)).kind is DecisionKind.CONFLICT
    assert decide_app_write(stored, _doc(has_migrations=False)).kind is DecisionKind.NOOP


def test_deployed_differing_non_null_conflict() -> None:
    stored = StoredDoc("deployed", _doc(deployment_pr="https://x"))
    assert decide_app_write(stored, _doc(git_sha="b" * 40)) == Decision(
        DecisionKind.CONFLICT, ("git_sha", "deployment_pr")
    )
    # incoming empty against a stored value also differs
    assert decide_app_write(stored, _doc(images=[])).fields == ("deployment_pr", "images")


def test_conflict_wins_over_fill() -> None:
    stored = StoredDoc("failed", _doc(has_migrations=None))
    got = decide_app_write(stored, _doc(git_sha="b" * 40, has_migrations=True))
    assert got == Decision(DecisionKind.CONFLICT, ("git_sha",))


def test_deployed_identical_noop() -> None:
    assert decide_app_write(StoredDoc("deployed", _doc()), _doc()).kind is DecisionKind.NOOP


def test_failed_same_as_deployed() -> None:
    assert decide_app_write(StoredDoc("failed", _empty()), _doc()).kind is DecisionKind.ENRICH
    assert decide_app_write(StoredDoc("failed", _doc()), _doc(git_sha="c" * 40)).kind is (
        DecisionKind.CONFLICT
    )


def test_list_order_matters() -> None:
    two = [*IMG, {"name": "b/c", "digest": "sha256:" + "b" * 64}]
    stored = StoredDoc("deployed", _doc(images=two))
    assert decide_app_write(stored, _doc(images=two[::-1])).kind is DecisionKind.CONFLICT


def test_conflict_lists_field_names_only() -> None:
    got = decide_app_write(StoredDoc("deployed", _doc()), _doc(git_sha="d" * 40))
    assert all(f in FIELDS for f in got.fields)
