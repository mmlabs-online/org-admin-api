"""Declares the admin permission a route needs.

ADM-002 turns each declaration into `require_permission(key)`.
"""

from typing import Any

PERMISSION_KEY = "x-permission"


def permission(key: str) -> dict[str, Any]:
    return {PERMISSION_KEY: key}
