import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

PASSWORD_MAX_LENGTH = 128


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: SecretStr = Field(max_length=PASSWORD_MAX_LENGTH)
    turnstile_token: str = Field(min_length=1, max_length=2048)


class SignupResponse(BaseModel):
    id: uuid.UUID
    email: str
    email_verified: bool
    created_at: datetime
