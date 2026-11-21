"""Permission scope rules (ADR-0015): pure functions, no ORM."""

from app.modules.rbac.models import OrgMode, PermissionScope


def reserved_scope(app_key: str, key: str) -> PermissionScope | None:
    """Scope fixed by the key: `org:create` is checked at app scope; `org:invite` and
    `org:remove-member` in the org."""
    if key == f"{app_key}:org:create":
        return PermissionScope.APP
    if key in (f"{app_key}:org:invite", f"{app_key}:org:remove-member"):
        return PermissionScope.ORG
    return None


def default_scope(app_key: str, org_mode: str, key: str) -> PermissionScope:
    """Scope when none is given: reserved key first, then fail closed (`org`) on org apps."""
    reserved = reserved_scope(app_key, key)
    if reserved is not None:
        return reserved
    return PermissionScope.ORG if org_mode == OrgMode.REQUIRED else PermissionScope.APP
