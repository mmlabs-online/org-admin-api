"""Pure invitation rules (USR-06b): the subset rule, safe org names."""

import pytest

from app.modules.invitations.rules import is_subset, safe_org_name


def test_is_subset_exact() -> None:
    assert is_subset(["calechip:data:read"], ["calechip:data:read", "calechip:org:edit"])


def test_is_subset_wildcard_granted() -> None:
    assert is_subset(["calechip:data:read", "calechip:org:edit"], ["calechip:*"])


def test_is_subset_missing_key() -> None:
    assert not is_subset(["calechip:data:read", "calechip:org:edit"], ["calechip:data:read"])


def test_is_subset_empty_role() -> None:
    assert is_subset([], [])


def test_safe_org_name_plain_ok() -> None:
    assert safe_org_name("  Acme Corp 2 ") == "Acme Corp 2"
    assert safe_org_name("Công ty Ánh Dương") == "Công ty Ánh Dương"


@pytest.mark.parametrize(
    "name",
    ["Visit https://evil.test", "www.evil", "ceo@acme", "evil.com", "see evil.io now"],
)
def test_safe_org_name_rejects_link_or_address(name: str) -> None:
    assert safe_org_name(name) is None


@pytest.mark.parametrize("name", ["Ac‮me", "Acme\nBcc: x", "Ac​me", "Acme\x00", "   "])
def test_safe_org_name_rejects_bidi_and_controls(name: str) -> None:
    assert safe_org_name(name) is None


def test_safe_org_name_rejects_over_100() -> None:
    assert safe_org_name("a" * 100) == "a" * 100
    assert safe_org_name("a" * 101) is None
