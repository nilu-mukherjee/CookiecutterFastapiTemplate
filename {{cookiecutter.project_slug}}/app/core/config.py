from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["dev", "staging", "prod"] = "dev"
    debug: bool = False
    log_level: str = "INFO"
{%- if cookiecutter.database == "postgresql" %}
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/app"
{%- elif cookiecutter.database == "mysql" %}
    database_url: str = "mysql+aiomysql://app:app@localhost:3306/app"
{%- else %}
    database_url: str = "sqlite+aiosqlite:///./app.db"
{%- endif %}
    cors_origins: list[str] = []
    allowed_hosts: list[str] = ["*"]
    rate_limit_per_minute: int = 120
{%- if cookiecutter.use_redis == "y" %}
    redis_url: str = "redis://localhost:6379/0"
{%- endif %}


@lru_cache
def get_settings() -> Settings:
    return Settings()
