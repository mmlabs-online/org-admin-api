"""ADM-038: the pure environment-by-version rule."""

import pytest

from app.modules.bug_reports.release_link import environment_for_version
from app.modules.releases.models import ReleaseEnvironment


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("v1.2.3", ReleaseEnvironment.PRODUCTION),
        ("v0.0.0", ReleaseEnvironment.PRODUCTION),
        ("sha-abc1234", ReleaseEnvironment.STAGING),
        ("1.2.3", None),
        ("V1.2.3", None),
        ("v01.2.3", None),
        ("sha-ABC1234", None),
        ("sha-abc123", None),
        ("v1.2.3 ", None),
        ("", None),
    ],
)
def test_environment_for_version(version: str, expected: ReleaseEnvironment | None) -> None:
    assert environment_for_version(version) == expected
