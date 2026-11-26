"""Pure org member removal rules (USR-30, ADR-0026): no ORM, unit-testable without a database."""

from collections.abc import Collection, Sequence
from collections.abc import Set as AbstractSet

from app.modules.invitations.rules import is_subset


def outranks(
    target_permission_keys: Collection[str],
    target_has_admin: bool,
    caller_permissions: AbstractSet[str] | Sequence[str],
) -> bool:
    """True when the target holds more than the caller may remove: an `is_admin` permission, or
    a permission the caller does not hold in the org (a `<app>:*` caller covers every key)."""
    return target_has_admin or not is_subset(target_permission_keys, caller_permissions)


def leaves_no_owner(target_is_owner: bool, other_owners: int) -> bool:
    """True when removing the target would leave the org without a counting owner."""
    return target_is_owner and other_owners == 0
