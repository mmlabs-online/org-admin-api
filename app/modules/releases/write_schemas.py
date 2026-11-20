"""Strict input models of the app release write (ADM-034, spec OQ36 / OQ45, ADR-0021 §4, §7).

Separate from the read schemas: `extra="forbid"` and `strict=True` at every level, so no field of
the record can be set that the app write does not own (`status`, `deployed_at`, `deploy_run_url`,
`created_via`, authors). Patterns are checked with `re.fullmatch` (never `$`, which accepts a
trailing newline). Every validator raises a fixed message and never echoes the submitted value.
"""

import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi.exceptions import RequestValidationError
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.core.text import DISALLOWED_CATEGORIES
from app.modules.releases.models import (
    MAX_CHANGES,
    MAX_IMAGES,
    PRODUCTION_VERSION_RE,
    STAGING_VERSION_RE,
    ReleaseEnvironment,
)
from app.modules.releases.schemas import ChangeRisk

_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PR_URL = re.compile(
    r"https://github\.com/mmlabs-online/([A-Za-z0-9_.-]{1,100})/pull/[1-9][0-9]{0,9}"
)
_DEPLOYMENT_PR = re.compile(r"https://github\.com/mmlabs-online/deployment/pull/[1-9][0-9]{0,9}")
DEPLOY_RUN_URL_FULL = re.compile(
    r"https://github\.com/mmlabs-online/deployment/actions/runs/[1-9][0-9]{0,19}"
    r"(/attempts/[1-9][0-9]{0,2})?"
)
_TICKET_ID = re.compile(r"[A-Z][A-Z0-9]{1,9}-[0-9]{1,6}[a-z]?")
# Docker repository name: optional registry host (with optional port), then path components.
_COMPONENT = r"[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*"
_HOST = r"(?:[a-z0-9]|[a-z0-9][a-z0-9-]*[a-z0-9])(?:\.(?:[a-z0-9]|[a-z0-9][a-z0-9-]*[a-z0-9]))*"
_IMAGE_NAME = re.compile(rf"(?:{_HOST}(?::[0-9]+)?/)?{_COMPONENT}(?:/{_COMPONENT})*")
_PRODUCTION_VERSION = re.compile(PRODUCTION_VERSION_RE.strip("^$"))
_STAGING_VERSION = re.compile(STAGING_VERSION_RE.strip("^$"))

MAX_TITLE = 200
MAX_IMAGE_NAME = 255


def _match(pattern: re.Pattern[str], code: str, message: str) -> AfterValidator:
    """Fullmatch check; `None` (an optional field sent as null) passes through."""

    def check(value: str | None) -> str | None:
        if value is not None and pattern.fullmatch(value) is None:
            raise PydanticCustomError(code, message)
        return value

    return AfterValidator(check)


def _clean_title(value: Any) -> Any:
    """NFC, strip control/format/separator code points, then trim; never rejects by itself."""
    if not isinstance(value, str):
        return value
    text = unicodedata.normalize("NFC", value)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in DISALLOWED_CATEGORIES)
    return text.strip()


def _pr_url_ok(value: str) -> str:
    match = _PR_URL.fullmatch(value)
    if match is None or match.group(1) in {".", ".."}:
        raise PydanticCustomError("pr_url", "must be a mmlabs-online GitHub pull request URL")
    return value


class ReleaseImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: Annotated[
        str,
        Field(max_length=MAX_IMAGE_NAME),
        _match(_IMAGE_NAME, "image_name", "must be a Docker repository name without tag or digest"),
    ]
    digest: Annotated[str, _match(_DIGEST, "image_digest", 'must be "sha256:<64 lowercase hex>"')]


class ReleaseChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pr_url: Annotated[str, AfterValidator(_pr_url_ok)]
    title: Annotated[str, BeforeValidator(_clean_title), Field(min_length=1, max_length=MAX_TITLE)]
    ticket_id: Annotated[str | None, _match(_TICKET_ID, "ticket_id", "must look like ABC-123")] = (
        None
    )
    risk: ChangeRisk | None = None


def _unique_image_names(images: list[ReleaseImageIn]) -> list[ReleaseImageIn]:
    if len({i.name for i in images}) != len(images):
        raise PydanticCustomError("image_names_unique", "image names must be unique")
    return images


def _unique_pr_urls(changes: list[ReleaseChangeIn]) -> list[ReleaseChangeIn]:
    if len({c.pr_url for c in changes}) != len(changes):
        raise PydanticCustomError("pr_urls_unique", "pr_url must be unique per release")
    return changes


class ReleasePut(BaseModel):
    """Body of `PUT /apps/{key}/releases/{environment}/{version}`: untrusted CI-reported data."""

    model_config = ConfigDict(extra="forbid", strict=True)

    git_sha: Annotated[str, _match(_GIT_SHA, "git_sha", "must be 40 lowercase hex characters")]
    deployment_pr: Annotated[
        str | None,
        _match(_DEPLOYMENT_PR, "deployment_pr", "must be a mmlabs-online deployment pull request"),
    ] = None
    has_migrations: bool | None = None
    images: Annotated[
        list[ReleaseImageIn],
        Field(min_length=1, max_length=MAX_IMAGES),
        AfterValidator(_unique_image_names),
    ]
    changes: Annotated[
        list[ReleaseChangeIn],
        Field(max_length=MAX_CHANGES),
        AfterValidator(_unique_pr_urls),
    ] = Field(default_factory=list)


class ReleaseDeploymentReport(BaseModel):
    """Body of `PUT .../deployment` (ADM-036, OQ38 / OQ45): the deployer's three fields only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["deployed", "failed"]
    deploy_run_url: Annotated[
        str,
        Field(max_length=255),
        _match(
            DEPLOY_RUN_URL_FULL,
            "deploy_run_url",
            "must be a mmlabs-online/deployment GitHub Actions run URL",
        ),
    ]
    deployed_at: AwareDatetime | None = Field(default=None, validate_default=True)

    @field_validator("deployed_at")
    @classmethod
    def _deployed_at_matches_status(
        cls, value: datetime | None, info: ValidationInfo
    ) -> datetime | None:
        status = info.data.get("status")
        if status == "deployed" and value is None:
            raise PydanticCustomError("deployed_at_required", "is required with status deployed")
        if status == "failed" and value is not None:
            raise PydanticCustomError("deployed_at_not_allowed", "is refused with status failed")
        return None if value is None else value.astimezone(UTC)


def _error(error_type: str, loc: tuple[str, ...], msg: str) -> RequestValidationError:
    return RequestValidationError([{"type": error_type, "loc": loc, "msg": msg}])


def check_release_path(environment: ReleaseEnvironment, version: str) -> None:
    """The version must match the environment's grammar (OQ36); 422 at `("path", "version")`."""
    pattern = (
        _PRODUCTION_VERSION if environment is ReleaseEnvironment.PRODUCTION else _STAGING_VERSION
    )
    if pattern.fullmatch(version) is None:
        raise _error(
            "release_version", ("path", "version"), "does not match the environment's grammar"
        )


def check_staging_sha(environment: ReleaseEnvironment, version: str, body: ReleasePut) -> None:
    """A staging version is `sha-` + the first 7 characters of `git_sha` (OQ36)."""
    if environment is ReleaseEnvironment.STAGING and version != f"sha-{body.git_sha[:7]}":
        raise _error("staging_version_sha", ("body", "git_sha"), "does not match the version")
