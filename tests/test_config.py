import pytest
from pydantic import ValidationError

from app.core.config import Settings

FLAGS = ["admin_users_api_enabled", "admin_rbac_api_enabled"]


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_rbac_flag_refused_in_staging_and_production(app_env: str) -> None:
    with pytest.raises(ValidationError, match="ADMIN_RBAC_API_ENABLED"):
        Settings(
            app_env=app_env,  # type: ignore[arg-type]
            admin_rbac_api_enabled=True,
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
