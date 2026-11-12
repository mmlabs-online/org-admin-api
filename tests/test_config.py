import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.conftest import TEST_JWT_SIGNING_KEY

FLAGS = ["admin_users_api_enabled", "admin_rbac_api_enabled"]


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_rbac_flag_refused_in_staging_and_production(app_env: str) -> None:
    with pytest.raises(ValidationError, match="ADMIN_RBAC_API_ENABLED"):
        Settings(
            app_env=app_env,  # type: ignore[arg-type]
            admin_rbac_api_enabled=True,
            trusted_proxy_cidrs=["172.18.0.0/16"],
            argon2_memory_kib=65536,  # meet the Argon2 floor so only the flag fails
            argon2_time_cost=3,
        )


@pytest.mark.parametrize("flag", FLAGS)
def test_admin_flag_requires_explicit_app_env(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    monkeypatch.delenv("APP_ENV")
    with pytest.raises(ValidationError, match="APP_ENV"):
        Settings(_env_file=None, **{flag: True})  # type: ignore[call-arg]


@pytest.mark.parametrize("flag", FLAGS)
def test_admin_flag_empty_app_env_refused(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    monkeypatch.setenv("APP_ENV", "")
    with pytest.raises(ValidationError, match="APP_ENV"):
        Settings(_env_file=None, **{flag: True})  # type: ignore[call-arg]


def test_no_flag_without_app_env_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV")
    assert Settings(_env_file=None).app_env == "local"  # type: ignore[call-arg]


def test_flags_with_explicit_app_env_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    from_env = Settings(_env_file=None, admin_users_api_enabled=True, admin_rbac_api_enabled=True)  # type: ignore[call-arg]
    assert from_env.app_env == "test"

    monkeypatch.delenv("APP_ENV")
    from_kwarg = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="local",
        admin_users_api_enabled=True,
        admin_rbac_api_enabled=True,
    )
    assert from_kwarg.app_env == "local"


# --- TRUSTED_PROXY_CIDRS gate (ADM-011) --------------------------------------

_PROD_ARGON2 = {"argon2_memory_kib": 65536, "argon2_time_cost": 3}


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_trusted_proxy_cidrs_required_in_staging_and_production(app_env: str) -> None:
    with pytest.raises(ValidationError, match="TRUSTED_PROXY_CIDRS"):
        Settings(app_env=app_env, trusted_proxy_cidrs=[], **_PROD_ARGON2)  # type: ignore[arg-type]


@pytest.mark.parametrize("app_env", ["staging", "production"])
@pytest.mark.parametrize(
    "cidrs", [["0.0.0.0/0"], ["::/0"], ["8.8.8.0/24"], ["10.0.0.0/7"], ["fd00::/40"]]
)
def test_trusted_proxy_cidrs_rules_in_staging_and_production(
    app_env: str, cidrs: list[str]
) -> None:
    with pytest.raises(ValidationError, match="TRUSTED_PROXY_CIDRS"):
        Settings(app_env=app_env, trusted_proxy_cidrs=cidrs, **_PROD_ARGON2)  # type: ignore[arg-type]


@pytest.mark.parametrize("cidrs", [["172.18.0.0/16"], ["172.31.250.2/32"], ["fd00:1::/64"]])
def test_trusted_proxy_cidrs_valid_in_production(cidrs: list[str]) -> None:
    settings = Settings(app_env="production", trusted_proxy_cidrs=cidrs, **_PROD_ARGON2)  # type: ignore[arg-type]
    assert settings.trusted_proxy_cidrs == cidrs


@pytest.mark.parametrize("app_env", ["local", "test"])
@pytest.mark.parametrize("cidrs", [[], ["0.0.0.0/0"]])
def test_trusted_proxy_cidrs_optional_in_local_and_test(app_env: str, cidrs: list[str]) -> None:
    settings = Settings(app_env=app_env, trusted_proxy_cidrs=cidrs)  # type: ignore[arg-type]
    assert settings.trusted_proxy_cidrs == cidrs


def test_settings_errors_do_not_echo_input(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "Sup3rS3cretMarker"
    monkeypatch.setenv("DATABASE_URL", f"postgresql+asyncpg://u:{marker}@db:5432/org_admin")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ARGON2_MEMORY_KIB", "65536")
    monkeypatch.setenv("ARGON2_TIME_COST", "3")
    monkeypatch.delenv("TRUSTED_PROXY_CIDRS", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "TRUSTED_PROXY_CIDRS" in str(exc.value)
    assert marker not in str(exc.value)


# --- IDENTITY_HMAC_KEY and CORS_ORIGINS (USR-03) -----------------------------

_PROD = {"trusted_proxy_cidrs": ["172.18.0.0/16"], **_PROD_ARGON2}


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_identity_hmac_key_required_in_production(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    monkeypatch.delenv("IDENTITY_HMAC_KEY", raising=False)
    with pytest.raises(ValidationError, match="IDENTITY_HMAC_KEY"):
        Settings(_env_file=None, app_env=app_env, **_PROD)  # type: ignore[call-arg, arg-type]
    ok = Settings(_env_file=None, app_env=app_env, identity_hmac_key="k" * 32, **_PROD)  # type: ignore[call-arg, arg-type]
    assert ok.identity_hmac_key is not None


@pytest.mark.parametrize("app_env", ["local", "production"])
def test_identity_hmac_key_too_short(app_env: str) -> None:
    with pytest.raises(ValidationError, match="IDENTITY_HMAC_KEY") as exc:
        Settings(app_env=app_env, identity_hmac_key="k" * 31, **_PROD)  # type: ignore[arg-type]
    assert "k" * 31 not in str(exc.value)


def test_identity_hmac_key_optional_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IDENTITY_HMAC_KEY", raising=False)
    assert Settings(_env_file=None, app_env="local").identity_hmac_key is None  # type: ignore[call-arg]


@pytest.mark.parametrize("origins", [["*"], ["http://localhost:3000", "*"]])
def test_cors_origins_wildcard_refused(origins: list[str]) -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(cors_origins=origins)


# --- JWT signing key, issuer and TTL (USR-03c) --------------------------------

_PROD_JWT = {**_PROD, "identity_hmac_key": "k" * 32}
_ISSUER = "https://api.mmlabs.online"


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_jwt_signing_key_required_in_production(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    with pytest.raises(ValidationError, match="JWT_SIGNING_KEY"):
        Settings(_env_file=None, app_env=app_env, jwt_issuer=_ISSUER, **_PROD_JWT)  # type: ignore[call-arg, arg-type]
    ok = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env=app_env,  # type: ignore[arg-type]
        jwt_issuer=_ISSUER,
        jwt_signing_key=TEST_JWT_SIGNING_KEY,  # type: ignore[arg-type]
        **_PROD_JWT,  # type: ignore[arg-type]
    )
    assert ok.jwt_signing_key is not None


@pytest.mark.parametrize("field", ["jwt_signing_key", "jwt_public_keys_extra"])
def test_jwt_signing_key_malformed_rejected_without_echo(field: str) -> None:
    marker = "Sup3rS3cretJwtMarker"
    value: object = marker if field == "jwt_signing_key" else [marker]
    with pytest.raises(ValidationError, match=field.upper()) as exc:
        Settings(**{field: value})
    assert marker not in str(exc.value)


@pytest.mark.parametrize("app_env", ["staging", "production"])
@pytest.mark.parametrize(
    "issuer",
    [None, "http://api.mmlabs.online", "https://api.mmlabs.online/", "https://api.x:8443", ""],
)
def test_jwt_issuer_required_in_production(
    monkeypatch: pytest.MonkeyPatch, app_env: str, issuer: str | None
) -> None:
    monkeypatch.delenv("JWT_ISSUER", raising=False)
    kwargs: dict[str, object] = {} if issuer is None else {"jwt_issuer": issuer}
    with pytest.raises(ValidationError, match="JWT_ISSUER"):
        Settings(_env_file=None, app_env=app_env, **kwargs, **_PROD_JWT)  # type: ignore[call-arg, arg-type]
    ok = Settings(_env_file=None, app_env=app_env, jwt_issuer=_ISSUER, **_PROD_JWT)  # type: ignore[call-arg, arg-type]
    assert ok.jwt_issuer == _ISSUER


def test_jwt_keys_optional_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("JWT_ISSUER", raising=False)
    settings = Settings(_env_file=None, app_env="local")  # type: ignore[call-arg]
    assert settings.jwt_signing_key is None
    assert settings.jwt_issuer == "http://localhost:8000"


@pytest.mark.parametrize("ttl", [59, 3601])
def test_access_token_ttl_bounds(ttl: int) -> None:
    with pytest.raises(ValidationError, match="access_token_ttl_seconds"):
        Settings(access_token_ttl_seconds=ttl)
