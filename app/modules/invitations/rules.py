"""Pure invitation rules (USR-06b, ADR-0014): no ORM, unit-testable without a database."""

import re
from collections.abc import Collection, Sequence
from collections.abc import Set as AbstractSet

from app.modules.audit.service import _CONTROL_CHARS
from app.modules.rbac.authz import permits

_ORG_NAME_MAX = 100
_DOMAIN_LIKE = re.compile(r"\w\.[a-z]{2,}", re.IGNORECASE)


def roles_overlap(app_key: str, default_keys: Collection[str], owner_keys: Collection[str]) -> bool:
    """True if the default role and the org owner role share a permission other than
    `<app>:org:create` (the S1 guard: an app-scope default role must not carry org-level
    permissions into every org token)."""
    return bool((set(default_keys) & set(owner_keys)) - {f"{app_key}:org:create"})


def is_subset(role_keys: Collection[str], granted: AbstractSet[str] | Sequence[str]) -> bool:
    """Every permission of the role is held (exactly, or via the `<app>:*` wildcard)."""
    return all(permits(granted, key) for key in role_keys)


def safe_org_name(name: str) -> str | None:
    """The org name for the email body, or None when it could carry a link, address or
    spoofing characters (the name is user-chosen text sent from our address)."""
    if (
        not name.strip()
        or len(name) > _ORG_NAME_MAX
        or _CONTROL_CHARS.search(name)
        or "://" in name
        or "www." in name.lower()
        or "@" in name
        or _DOMAIN_LIKE.search(name)
    ):
        return None
    return name.strip()
