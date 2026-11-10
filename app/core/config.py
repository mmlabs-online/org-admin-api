from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "mmlabs org-admin-api"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_debug: bool = False
    api_prefix: str = "/api/v1"

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://postgres:postgres@localhost:5432/org_admin")
    )
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False

    cors_origins: list[str] = []


@lru_cache
def get_settings() -> Settings:
    return Settings()
