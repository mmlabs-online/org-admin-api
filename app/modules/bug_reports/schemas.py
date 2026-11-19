import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

from app.modules.bug_reports import text
from app.modules.bug_reports.models import BugReportStatus


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
