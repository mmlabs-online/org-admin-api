import ipaddress
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# OWASP Password Storage Cheat Sheet minimum for Argon2id (m=19 MiB, t=2).
_ARGON2_MIN_MEMORY_KIB = 19456
_ARGON2_MIN_TIME_COST = 2


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    app_name: str = "mmlabs org-admin-api"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_debug: bool = False
    app_version: str = "dev"  # set by the release build (APP_VERSION)
    api_prefix: str = "/api/v1"

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://postgres:postgres@localhost:5432/org_admin")
    )
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False

    cors_origins: list[str] = []

    # Cloudflare Turnstile (sign-up). Missing secret => sign-up fails closed (403).
    turnstile_secret_key: SecretStr | None = None
    turnstile_allowed_hostnames: list[str] = []  # empty = hostname not checked

    # Peers allowed to set CF-Connecting-IP (e.g. cloudflared's Docker network).
    trusted_proxy_cidrs: list[str] = []

    signup_rate_limit: int = Field(default=20, ge=1)
    signup_rate_window_seconds: int = Field(default=60, ge=1)
    # Public role catalog (D17); app backends may share one egress IP.
    role_catalog_rate_limit: int = Field(default=120, ge=1)
    role_catalog_rate_window_seconds: int = Field(default=60, ge=1)

    password_min_length: int = Field(default=12, ge=8)

    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_kib: int = Field(default=65536, ge=8)
    argon2_parallelism: int = Field(default=4, ge=1)
    argon2_max_concurrency: int = Field(default=2, ge=1)

    # Temporary: exposes user PII and a disable action without auth. Removed by ADM-002.
    admin_users_api_enabled: bool = False
    # Temporary: privilege-changing RBAC endpoints without auth. Removed by ADM-002.
    admin_rbac_api_enabled: bool = False

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _valid_cidrs(cls, value: list[str]) -> list[str]:
        for cidr in value:
            ipaddress.ip_network(cidr, strict=False)
        return value

    @model_validator(mode="after")
    def _argon2_floor(self) -> Self:
        if self.app_env in {"staging", "production"} and (
            self.argon2_memory_kib < _ARGON2_MIN_MEMORY_KIB
            or self.argon2_time_cost < _ARGON2_MIN_TIME_COST
        ):
            raise ValueError("Argon2 parameters are below the minimum for staging/production")
        return self

    @model_validator(mode="after")
    def _admin_apis_gate(self) -> Self:
        flags = {
            "ADMIN_USERS_API_ENABLED": self.admin_users_api_enabled,
            "ADMIN_RBAC_API_ENABLED": self.admin_rbac_api_enabled,
        }
        for name, on in flags.items():
            if on and self.app_env in {"staging", "production"}:
                raise ValueError(f"{name} must not be set in staging/production before ADM-002")
        # `env_ignore_empty=True`: an empty `APP_ENV=` counts as unset.
        if any(flags.values()) and "app_env" not in self.model_fields_set:
            raise ValueError("APP_ENV must be set explicitly when an admin API flag is on")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
