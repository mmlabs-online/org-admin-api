"""Permission scope rules (USR-06c, ADR-0015): pure, no database."""

from app.modules.rbac.models import PermissionScope
from app.modules.rbac.scopes import default_scope, reserved_scope


def test_reserved_scope_create_is_app_invite_is_org() -> None:
    assert reserved_scope("calechip", "calechip:org:create") == PermissionScope.APP
    assert reserved_scope("calechip", "calechip:org:invite") == PermissionScope.ORG


def test_reserved_scope_other_keys_and_other_apps_are_free() -> None:
    assert reserved_scope("calechip", "calechip:org:edit") is None
    assert reserved_scope("calechip", "other:org:create") is None


def test_default_scope_org_app_fails_closed() -> None:
    assert default_scope("calechip", "required", "calechip:data:read") == PermissionScope.ORG


def test_default_scope_none_app_is_app() -> None:
    assert default_scope("stoktok", "none", "stoktok:data:read") == PermissionScope.APP


def test_default_scope_reserved_key_wins() -> None:
    assert default_scope("calechip", "required", "calechip:org:create") == PermissionScope.APP
    assert default_scope("calechip", "none", "calechip:org:invite") == PermissionScope.ORG


def test_reserved_scope_remove_member_is_org() -> None:
    assert reserved_scope("calechip", "calechip:org:remove-member") == PermissionScope.ORG
    assert reserved_scope("calechip", "other:org:remove-member") is None
    assert default_scope("calechip", "none", "calechip:org:remove-member") == PermissionScope.ORG
