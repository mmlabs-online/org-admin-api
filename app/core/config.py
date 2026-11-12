import ipaddress
import re
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.jwt_keys import build_key_set

# OWASP Password Storage Cheat Sheet minimum for Argon2id (m=19 MiB, t=2).
_ARGON2_MIN_MEMORY_KIB = 19456
_ARGON2_MIN_TIME_COST = 2
_IDENTITY_HMAC_KEY_MIN_LENGTH = 32
_JWT_ISSUER_RE = r"^https://[a-z0-9.-]+$"  # staging/production: no path, port or trailing slash


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,  # never print env values (e.g. DATABASE_URL) in errors (R5)
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
    login_rate_limit: int = Field(default=20, ge=1)
    login_rate_window_seconds: int = Field(default=60, ge=1)
    token_rate_limit: int = Field(default=60, ge=1)
    token_rate_window_seconds: int = Field(default=60, ge=1)
    # JWKS: app backends may share one egress IP.
    jwks_rate_limit: int = Field(default=120, ge=1)
    jwks_rate_window_seconds: int = Field(default=60, ge=1)

    # Access-token signing (ADR-0005): base64 of a PEM P-256 private key on one line. Required
    # in staging/production; local/test fall back to an ephemeral per-process key.
    jwt_signing_key: SecretStr | None = None
    # JSON list of base64 public PEMs published in the JWKS during a rotation (not secret).
    jwt_public_keys_extra: list[str] = []
    jwt_issuer: str = "http://localhost:8000"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)

    # Device sessions (ADR-0004): idle timeout (= cookie Max-Age) and absolute lifetime.
    device_session_idle_days: int = Field(default=30, ge=1)
    device_session_max_days: int = Field(default=365, ge=1)
    # Keys the per-email login failure counters (HMAC-SHA256, ADR-0002 §5). Required in
    # staging/production; local/test fall back to a random per-process key.
    identity_hmac_key: SecretStr | None = None

    password_min_length: int = Field(default=12, ge=8)

    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_kib: int = Field(default=65536, ge=8)
    argon2_parallelism: int = Field(default=4, ge=1)
    argon2_max_concurrency: int = Field(default=2, ge=1)

    # Temporary: exposes user PII and a disable action without auth. Removed by ADM-002.
    admin_users_api_enabled: bool = False
    # Temporary: privilege-changing RBAC endpoints without auth. Removed by ADM-002.
    admin_rbac_api_enabled: bool = False

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origin(cls, value: list[str]) -> list[str]:
        # With credentials, Starlette would echo any Origin for "*".
        if "*" in value:
            raise ValueError("CORS_ORIGINS must not contain '*'")
        return value

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _valid_cidrs(cls, value: list[str]) -> list[str]:
        for cidr in value:
            ipaddress.ip_network(cidr, strict=False)
        return value

    @model_validator(mode="after")
    def _trusted_proxy_gate(self) -> Self:
        if self.app_env not in {"staging", "production"}:
            return self
        if not self.trusted_proxy_cidrs:
            raise ValueError(
                "TRUSTED_PROXY_CIDRS must be set in staging/production (JSON list of the "
                "cloudflared network CIDRs, see docs/deploy.md)"
            )
        for cidr in self.trusted_proxy_cidrs:
            net = ipaddress.ip_network(cidr, strict=False)
            min_prefix = 8 if net.version == 4 else 48
            if net.prefixlen == 0 or not net.is_private or net.prefixlen < min_prefix:
                raise ValueError(
                    "TRUSTED_PROXY_CIDRS entries must be private networks of at most /8 (IPv4) "
                    "or /48 (IPv6) in staging/production"
                )
        return self

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

    @model_validator(mode="after")
    def _identity_hmac_key_gate(self) -> Self:
        key = self.identity_hmac_key.get_secret_value() if self.identity_hmac_key else ""
        if key and len(key) < _IDENTITY_HMAC_KEY_MIN_LENGTH:
            raise ValueError("IDENTITY_HMAC_KEY must be at least 32 characters")
        if not key and self.app_env in {"staging", "production"}:
            raise ValueError("IDENTITY_HMAC_KEY must be set in staging/production")
        return self

    @model_validator(mode="after")
    def _jwt_keys_gate(self) -> Self:
        key = self.jwt_signing_key.get_secret_value() if self.jwt_signing_key else None
        if key is not None:
            try:
                build_key_set(key, [])
            except ValueError:
                raise ValueError(
                    "JWT_SIGNING_KEY is not a valid base64 PEM P-256 private key"
                ) from None
        if self.jwt_public_keys_extra:
            try:
                build_key_set(key, self.jwt_public_keys_extra)
            except ValueError:
                raise ValueError(
                    "JWT_PUBLIC_KEYS_EXTRA must be a JSON list of distinct base64 PEM P-256 "
                    "public keys, none equal to the signing key"
                ) from None
        if self.app_env in {"staging", "production"}:
            if key is None:
                raise ValueError("JWT_SIGNING_KEY must be set in staging/production")
            if "jwt_issuer" not in self.model_fields_set or not re.fullmatch(
                _JWT_ISSUER_RE, self.jwt_issuer
            ):
                raise ValueError(
                    "JWT_ISSUER must be set in staging/production to https://<host> "
                    "(no path, port or trailing slash)"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
