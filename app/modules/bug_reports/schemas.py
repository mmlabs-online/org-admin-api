import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.modules.attachments.store import ImageType
from app.modules.bug_reports import text
from app.modules.bug_reports.models import BugReportStatus

UNTRUSTED = (
    "Untrusted plain text supplied by the reporter's app (or staff), returned verbatim. It may "
    "contain HTML metacharacters (< > & \" ') and invisible or bidi characters. Render it as "
    "text, never as HTML."
)
UNTRUSTED_URL = (
    UNTRUSTED + " http(s) only (enforced at submit); re-check the scheme before rendering it as a "
    "link."
)


def _printable_ua(value: str) -> str:
    return text.check_printable(value, text.USER_AGENT_MAX)


class BugReportCreate(BaseModel):
    """The app comes from `X-MM-App`, never from the body; unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    description: Annotated[str, AfterValidator(text.clean_description)]
    org_id: uuid.UUID | None = None
    page_url: Annotated[str, AfterValidator(text.reduce_page_url)] | None = None
    app_version: Annotated[str, AfterValidator(text.check_app_version)] | None = None
    user_agent: Annotated[str, AfterValidator(_printable_ua)] | None = None


class BugReportCreated(BaseModel):
    id: uuid.UUID
    status: BugReportStatus
    created_at: datetime


class BugReportReporter(BaseModel):
    """The reporter's account. `email` is PII."""

    id: uuid.UUID
    display_name: str | None = Field(description=UNTRUSTED)
    email: str


class BugReportHandler(BaseModel):
    """The staff account that marked the report done (no email)."""

    id: uuid.UUID
    display_name: str | None = Field(description=UNTRUSTED)


class BugReportListItem(BaseModel):
    id: uuid.UUID
    app_key: str
    org_id: uuid.UUID | None
    status: BugReportStatus
    reporter: BugReportReporter
    description_preview: str = Field(description="First 200 characters. " + UNTRUSTED)
    handler: BugReportHandler | None = Field(
        description="Null when open, or when the handler account was deleted."
    )
    handled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BugReportAttachmentRead(BaseModel):
    """An attached image. No Drive id and no filename: files are fetched only through
    `GET /api/v1/bug-reports/{id}/attachments/{attachment_id}/content`."""

    id: uuid.UUID
    content_type: ImageType
    size_bytes: int
    created_at: datetime


class BugReportRead(BaseModel):
    id: uuid.UUID
    app_key: str
    org_id: uuid.UUID | None
    status: BugReportStatus
    reporter: BugReportReporter
    description: str = Field(description=UNTRUSTED)
    page_url: str | None = Field(description=UNTRUSTED_URL)
    app_version: str | None = Field(description=UNTRUSTED)
    user_agent: str | None = Field(description=UNTRUSTED)
    resolution_note: str | None = Field(description="Staff only. " + UNTRUSTED)
    handler: BugReportHandler | None = Field(
        description="Null when open, or when the handler account was deleted."
    )
    handled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: list[BugReportAttachmentRead] = Field(
        default_factory=list, description="Images of the report, oldest first."
    )


class BugReportUpdate(BaseModel):
    """Absent field: unchanged. `resolution_note: null` clears the note; a blank note is 422."""

    model_config = ConfigDict(extra="forbid")

    status: BugReportStatus | None = None
    resolution_note: Annotated[str, AfterValidator(text.clean_resolution_note)] | None = Field(
        default=None, description="Staff only. " + UNTRUSTED
    )

    @model_validator(mode="after")
    def _check_fields(self) -> "BugReportUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status must not be null")
        return self
