"""Which release a report links to (ADM-038, ADR-0028). Pure rules, no I/O."""

import re

from app.modules.releases.models import (
    PRODUCTION_VERSION_RE,
    STAGING_VERSION_RE,
    ReleaseEnvironment,
)

# The spec (§5.12, OQ50 default): the inferred rule uses production releases only.
INFERRED_ENVIRONMENT = ReleaseEnvironment.PRODUCTION


def environment_for_version(version: str) -> ReleaseEnvironment | None:
    """`vX.Y.Z` is production, `sha-<7 hex>` is staging; anything else (also a missing `v`,
    other case, surrounding spaces) is None. Exact match, no normalization."""
    if re.fullmatch(PRODUCTION_VERSION_RE, version):
        return ReleaseEnvironment.PRODUCTION
    if re.fullmatch(STAGING_VERSION_RE, version):
        return ReleaseEnvironment.STAGING
    return None
