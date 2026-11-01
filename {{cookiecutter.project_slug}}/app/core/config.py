{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
from functools import lru_cache
from typing import Literal

{% if auth %}from pydantic import model_validator
{% endif %}from pydantic_settings import BaseSettings, SettingsConfigDict
{%- if auth %}

PLACEHOLDER_SECRET = "change-me"
MIN_SECRET_LENGTH = 32
{%- endif %}


class Settings(BaseSettings):
    # hide_input_in_errors: a validation error must not echo secrets (keys, DB URLs).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

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
{%- if auth %}
    # Required, no default: signs tokens. Must be strong outside dev (see below).
    secret_key: str
    first_superuser_email: str | None = None
    first_superuser_password: str | None = None
{%- endif %}
{%- if bearer %}
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
{%- endif %}
{%- if auth %}

    @model_validator(mode="after")
    def _require_strong_secret_outside_dev(self) -> "Settings":
        weak = self.secret_key == PLACEHOLDER_SECRET or len(self.secret_key) < MIN_SECRET_LENGTH
        if self.environment != "dev" and weak:
            raise ValueError(
                f"SECRET_KEY must be a random value of at least {MIN_SECRET_LENGTH} "
                f"characters when ENVIRONMENT is {self.environment!r}"
            )
        return self
{%- endif %}


@lru_cache
def get_settings() -> Settings:
{%- if auth %}
    # Required fields (secret_key) come from the environment, which mypy cannot see.
    return Settings()  # type: ignore[call-arg]
{%- else %}
    return Settings()
{%- endif %}
