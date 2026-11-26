import ipaddress
import re
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.jwt_keys import build_key_set

# OWASP Password Storage Cheat Sheet minimum for Argon2id (m=19 MiB, t=2).
_ARGON2_MIN_MEMORY_KIB = 19456
_ARGON2_MIN_TIME_COST = 2
_IDENTITY_HMAC_KEY_MIN_LENGTH = 32
_JWT_ISSUER_RE = r"^https://[a-z0-9.-]+$"  # staging/production: no path, port or trailing slash
_DRIVE_KEY_NAMES = (
    "GOOGLE_DRIVE_CLIENT_ID",
    "GOOGLE_DRIVE_CLIENT_SECRET",
    "GOOGLE_DRIVE_REFRESH_TOKEN",
    "GOOGLE_DRIVE_ROOT_FOLDER_ID",
)
_DRIVE_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9.-]+\.apps\.googleusercontent\.com")
_DRIVE_SECRET_RE = re.compile(r"[\x21-\x7e]{1,2048}")
_DRIVE_FOLDER_ID_RE = re.compile(r"[A-Za-z0-9_-]{10,128}")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


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
    accounts_rate_limit: int = Field(default=60, ge=1)
    accounts_rate_window_seconds: int = Field(default=60, ge=1)
    join_rate_limit: int = Field(default=20, ge=1)
    join_rate_window_seconds: int = Field(default=60, ge=1)
    # End-user org creation (USR-06a): per-IP window plus a per-user lifetime cap.
    org_create_rate_limit: int = Field(default=10, ge=1)
    org_create_rate_window_seconds: int = Field(default=3600, ge=1)
    orgs_list_rate_limit: int = Field(default=60, ge=1)
    orgs_list_rate_window_seconds: int = Field(default=60, ge=1)
    org_create_max_per_user: int = Field(default=20, ge=1)
    # Bug reports (ADM-028, ADR-0019): per-IP window, per-user in-process burst, DB-backed caps.
    bug_report_rate_limit: int = Field(default=20, ge=1)
    bug_report_rate_window_seconds: int = Field(default=3600, ge=1)
    bug_report_user_rate_limit: int = Field(default=3, ge=1)
    bug_report_user_rate_window_seconds: int = Field(default=60, ge=1)
    bug_report_max_per_user_day: int = Field(default=10, ge=1)
    bug_report_max_open_per_user_app: int = Field(default=10, ge=1)
    bug_report_attachment_rate_limit: int = Field(default=60, ge=1)
    bug_report_attachment_rate_window_seconds: int = Field(default=3600, ge=1)
    bug_report_attachment_user_rate_limit: int = Field(default=10, ge=1)
    bug_report_attachment_user_rate_window_seconds: int = Field(default=60, ge=1)
    bug_report_attachment_window_seconds: int = Field(default=900, ge=60, le=86400)
    join_reject_cooldown_seconds: int = Field(default=604800, ge=0)  # 0 = no cooldown
    # Join-request queue (USR-15): pending cap per approval app; retention of decided rows.
    join_max_pending_per_app: int = Field(default=1000, ge=1)
    join_request_retention_days: int = Field(default=180, ge=1)
    # Org invitations (USR-06b, ADR-0014): per-IP windows, TTL, per-inviter and per-org caps.
    invitation_create_rate_limit: int = Field(default=20, ge=1)
    invitation_create_rate_window_seconds: int = Field(default=3600, ge=1)
    invitation_accept_rate_limit: int = Field(default=20, ge=1)
    invitation_accept_rate_window_seconds: int = Field(default=600, ge=1)
    invitation_ttl_hours: int = Field(default=168, ge=1)
    invitation_max_per_inviter_per_day: int = Field(default=5, ge=1)
    invitation_max_pending_per_org: int = Field(default=50, ge=1)
    # JWKS: app backends may share one egress IP.
    jwks_rate_limit: int = Field(default=120, ge=1)
    jwks_rate_window_seconds: int = Field(default=60, ge=1)
    app_manifest_rate_limit: int = Field(default=30, ge=1)
    app_manifest_rate_window_seconds: int = Field(default=60, ge=1)
    # App release write (ADM-034, ADR-0021 §3): per IP, before auth; body cap and read deadline.
    app_release_rate_limit: int = Field(default=30, ge=1)
    app_release_rate_window_seconds: int = Field(default=60, ge=1)
    app_release_max_bytes: int = Field(default=262144, ge=1024)
    app_release_read_timeout_seconds: int = Field(default=10, ge=1)
    # Deploy report (ADM-036, ADR-0021 §3): per IP, before auth; body cap and read deadline.
    release_deployment_rate_limit: int = Field(default=30, ge=1)
    release_deployment_rate_window_seconds: int = Field(default=60, ge=1)
    release_deployment_max_bytes: int = Field(default=4096, ge=512)
    release_deployment_read_timeout_seconds: int = Field(default=10, ge=1)

    # Org member removal (USR-30, ADR-0026): per IP, and per signed-in user after the session check.
    org_member_remove_rate_limit: int = Field(default=30, ge=1)
    org_member_remove_rate_window_seconds: int = Field(default=3600, ge=1)
    org_member_remove_user_rate_limit: int = Field(default=10, ge=1)
    org_member_remove_user_rate_window_seconds: int = Field(default=600, ge=1)

    # Display name write (USR-33, D40): per IP, and per signed-in user after the session check.
    profile_rate_limit: int = Field(default=20, ge=1)
    profile_rate_window_seconds: int = Field(default=60, ge=1)
    profile_user_rate_limit: int = Field(default=10, ge=1)
    profile_user_rate_window_seconds: int = Field(default=3600, ge=1)

    # Org members read (USR-29, ADR-0025): per IP before auth, per credential after auth.
    app_org_members_rate_limit: int = Field(default=120, ge=1)
    app_org_members_rate_window_seconds: int = Field(default=60, ge=1)
    app_org_members_key_rate_limit: int = Field(default=60, ge=1)
    app_org_members_key_rate_window_seconds: int = Field(default=60, ge=1)

    # Usage metering (ADM-042, ADR-0022 §5, §7): per IP before auth, per IP for failed auth, per
    # key after auth (shared by the four usage routes); amount cap, body cap, read deadline and
    # the window in which a consume may still be settled or refunded.
    usage_rate_limit: int = Field(default=1200, ge=1)
    usage_rate_window_seconds: int = Field(default=60, ge=1)
    usage_auth_failed_rate_limit: int = Field(default=20, ge=1)
    usage_auth_failed_rate_window_seconds: int = Field(default=60, ge=1)
    usage_key_rate_limit: int = Field(default=600, ge=1)
    usage_key_rate_window_seconds: int = Field(default=60, ge=1)
    usage_max_amount_per_call: int = Field(default=1_000_000, ge=1, le=2**31 - 1)
    usage_max_bytes: int = Field(default=2048, ge=512)
    usage_read_timeout_seconds: int = Field(default=5, ge=1)
    usage_adjust_window_seconds: int = Field(default=86400, ge=60)
    # Per-use events are purged after this many days (OQ69, ADM-045); 90 is the floor (ADR-0022).
    usage_event_retention_days: int = Field(default=90, ge=90)

    # Access-token signing (ADR-0005): base64 of a PEM P-256 private key on one line. Required
    # in staging/production; local/test fall back to an ephemeral per-process key.
    jwt_signing_key: SecretStr | None = None
    # JSON list of base64 public PEMs published in the JWKS during a rotation (not secret).
    jwt_public_keys_extra: list[str] = []
    jwt_issuer: str = "http://localhost:8000"
    # ADR-0005 §6 (USR-13): production 43200; above needs a new decision
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=43200)

    # Device sessions (ADR-0004): idle timeout (= cookie Max-Age) and absolute lifetime.
    device_session_idle_days: int = Field(default=30, ge=1)
    device_session_max_days: int = Field(default=365, ge=1)
    # Keys the per-email login failure counters (HMAC-SHA256, ADR-0002 §5). Required in
    # staging/production; local/test fall back to a random per-process key.
    identity_hmac_key: SecretStr | None = None

    # Email (USR-04, ADR-0007): Gmail SMTP with STARTTLS. Username and App Password are
    # required in staging/production; locally, without them, nothing is sent. Links always use
    # the request's validated `Origin` (USR-04c), so there is no link-base setting.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    email_from: str | None = None  # defaults to SMTP_USERNAME
    # All verification/reset/invitation sends per 24 h: verify at most 70 %, reset 40 %,
    # invitations 10 % (USR-04b, USR-06b).
    email_daily_cap: int = Field(default=300, ge=1)
    verify_email_rate_limit: int = Field(default=20, ge=1)
    verify_email_rate_window_seconds: int = Field(default=60, ge=1)
    verify_email_resend_rate_limit: int = Field(default=5, ge=1)
    verify_email_resend_rate_window_seconds: int = Field(default=600, ge=1)
    forgot_rate_limit: int = Field(default=5, ge=1)
    forgot_rate_window_seconds: int = Field(default=60, ge=1)
    reset_rate_limit: int = Field(default=10, ge=1)
    reset_rate_window_seconds: int = Field(default=60, ge=1)

    # Bug-report attachments in Google Drive (ADM-027, ADR-0018): all four or none.
    google_drive_client_id: str | None = None
    google_drive_client_secret: SecretStr | None = None
    google_drive_refresh_token: SecretStr | None = None
    google_drive_root_folder_id: SecretStr | None = None

    password_min_length: int = Field(default=6, ge=6)  # owner, 2026-11-16: minimum 6 (was 12)

    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_kib: int = Field(default=65536, ge=8)
    argon2_parallelism: int = Field(default=4, ge=1)
    argon2_max_concurrency: int = Field(default=2, ge=1)

    # Swagger UI and `/openapi.json`: unset = on everywhere except production (ADM-002b).
    api_docs_enabled: bool | None = None

    @property
    def docs_enabled(self) -> bool:
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return self.app_env != "production"

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _valid_cidrs(cls, value: list[str]) -> list[str]:
        for cidr in value:
            ipaddress.ip_network(cidr, strict=False)
        return value

    @model_validator(mode="after")
    def _app_env_explicit_gate(self) -> Self:
        """Allow-list: the `local` default applies only to a DSN with exactly one loopback host
        and no query string or fragment; anything else needs an explicit `APP_ENV`, so a
        deployment never falls back to local behaviour. A query (`?host=`, `?hostaddr=`,
        `?dsn=`, `?service=`, ...) can redirect the driver, so any `?` refuses. pydantic puts
        `#?host=x` in the fragment but SQLAlchemy's `make_url` (what the engine receives)
        parses it as a query, hence the fragment and engine-view checks. The error never
        formats the URL (R5)."""
        if "app_env" in self.model_fields_set:
            return self
        message = "APP_ENV must be set explicitly when DATABASE_URL is not localhost"
        url = self.database_url
        hosts = [h.get("host") for h in url.hosts()]
        try:
            engine_url = make_url(str(url))
        except Exception:
            raise ValueError(message) from None
        if (
            len(hosts) != 1
            or hosts[0] not in _LOOPBACK_HOSTS
            or url.query is not None
            or url.fragment is not None
            or engine_url.query
            or engine_url.host not in _LOOPBACK_HOSTS
        ):
            raise ValueError(message)
        return self

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
    def _join_retention_covers_cooldown(self) -> Self:
        if self.join_request_retention_days * 86400 < self.join_reject_cooldown_seconds:
            raise ValueError(
                "JOIN_REQUEST_RETENTION_DAYS must be at least JOIN_REJECT_COOLDOWN_SECONDS"
            )
        return self

    @model_validator(mode="after")
    def _usage_retention_covers_adjust_window(self) -> Self:
        if self.usage_event_retention_days * 86400 < self.usage_adjust_window_seconds:
            raise ValueError("USAGE_EVENT_RETENTION_DAYS must cover USAGE_ADJUST_WINDOW_SECONDS")
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

    @model_validator(mode="after")
    def _email_gate(self) -> Self:
        if self.app_env in {"staging", "production"}:
            missing = [
                name
                for name, value in (
                    ("SMTP_USERNAME", self.smtp_username),
                    ("SMTP_PASSWORD", self.smtp_password),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"{', '.join(missing)} must be set in staging/production")
        return self

    @property
    def drive_enabled(self) -> bool:
        return all(
            (
                self.google_drive_client_id,
                self.google_drive_client_secret,
                self.google_drive_refresh_token,
                self.google_drive_root_folder_id,
            )
        )

    @model_validator(mode="after")
    def _drive_gate(self) -> Self:
        values = (
            self.google_drive_client_id,
            self.google_drive_client_secret,
            self.google_drive_refresh_token,
            self.google_drive_root_folder_id,
        )
        present = [bool(v) for v in values]
        if not any(present):
            return self
        if not all(present):
            missing = [n for n, ok in zip(_DRIVE_KEY_NAMES, present, strict=True) if not ok]
            raise ValueError(
                f"{', '.join(missing)} must be set together with the other GOOGLE_DRIVE_* keys"
                " (or none of them)"
            )
        raw = [v if isinstance(v, str) else v.get_secret_value() for v in values if v]
        checks = (
            (len(raw[0]) <= 255 and _DRIVE_CLIENT_ID_RE.fullmatch(raw[0])),
            _DRIVE_SECRET_RE.fullmatch(raw[1]),
            _DRIVE_SECRET_RE.fullmatch(raw[2]),
            _DRIVE_FOLDER_ID_RE.fullmatch(raw[3]),
        )
        for name, ok in zip(_DRIVE_KEY_NAMES, checks, strict=True):
            if not ok:
                raise ValueError(f"{name} is malformed")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
