import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator

PASSWORD_MAX_LENGTH = 128
EMAIL_TOKEN_RE = r"^[A-Za-z0-9_-]{32,128}$"  # noqa: S105 - a pattern, not a secret


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


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: SecretStr = Field(max_length=PASSWORD_MAX_LENGTH)
    turnstile_token: str | None = Field(default=None, min_length=1, max_length=2048)


class LoginUser(BaseModel):
    id: uuid.UUID
    email: str
    email_verified: bool


class LoginResponse(BaseModel):
    account_id: uuid.UUID
    user: LoginUser


class LogoutRequest(BaseModel):
    """Exactly one of `{account_id}` (that account) or `{all: true}` (the whole device)."""

    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID | None = None
    all: Literal[True] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.account_id is None) == (self.all is None):
            raise ValueError("exactly one of account_id or all is required")
        return self


class VerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(pattern=EMAIL_TOKEN_RE)


class ResendVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    turnstile_token: str = Field(min_length=1, max_length=2048)


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(pattern=EMAIL_TOKEN_RE)
    new_password: SecretStr = Field(max_length=PASSWORD_MAX_LENGTH)
