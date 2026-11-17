import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict


class JoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID


class JoinResponse(BaseModel):
    status: Literal["member"]


class JoinPendingResponse(BaseModel):
    """202: the app needs a staff decision (`join_policy = approval`)."""

    status: Literal["pending"]
    request_id: uuid.UUID
