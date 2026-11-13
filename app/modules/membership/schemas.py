import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict


class JoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID


class JoinResponse(BaseModel):
    status: Literal["member"]  # USR-03g adds "pending" (202)
