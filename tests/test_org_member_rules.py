"""Pure org member removal rules (USR-30): no database."""

import itertools

from app.modules.organizations.member_rules import leaves_no_owner, outranks


def test_outranks_false_when_subset_and_no_admin() -> None:
    assert not outranks(["a:x:read"], False, ["a:x:read", "a:x:write"])


def test_outranks_true_when_target_has_admin_permission() -> None:
    assert outranks(["a:x:read"], True, ["a:x:read", "a:x:admin"])


def test_outranks_true_when_target_permission_not_held() -> None:
    assert outranks(["a:x:read", "a:x:write"], False, ["a:x:read"])


def test_wildcard_caller_covers_everything_but_target_wildcard_needs_wildcard() -> None:
    assert not outranks(["a:x:read", "a:y:write"], False, ["a:*"])
    assert not outranks(["a:*"], False, ["a:*"])
    assert outranks(["a:*"], False, ["a:x:read"])
    assert outranks(["a:x:read"], False, ["b:*"])


def test_co_owners_equal_sets_do_not_outrank() -> None:
    keys = ["a:org:invite", "a:org:remove-member", "a:data:read"]
    assert not outranks(keys, False, keys)


def test_leaves_no_owner_only_when_target_is_owner_and_no_other() -> None:
    for is_owner, others in itertools.product([False, True], [0, 1, 2]):
        assert leaves_no_owner(is_owner, others) is (is_owner and others == 0)
