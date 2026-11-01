{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set cookie_auth = auth and cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set use_jwt = auth and (cookiecutter.use_auth_jwt_bearer == "y" or cookiecutter.use_auth_jwt_cookie == "y") -%}
from functools import lru_cache
from typing import Literal

{% if cookie_auth %}from pydantic import field_validator, model_validator
{% elif auth %}from pydantic import model_validator
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
{%- if use_jwt %}
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
{%- endif %}
{%- if cookie_auth %}
    # Attributes of the auth cookies. Secure is mandatory outside dev (see below);
    # Domain is only sent when set (host-only cookies by default).
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
{%- endif %}
{%- if cookie_auth %}

    @field_validator("cookie_domain", mode="before")
    @classmethod
    def _empty_domain_is_none(cls, value: object) -> object:
        # COOKIE_DOMAIN= (empty) means "no Domain attribute", not Domain="".
        return value or None
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
{%- if cookie_auth %}

    @model_validator(mode="after")
    def _require_secure_cookies(self) -> "Settings":
        if self.environment != "dev" and not self.cookie_secure:
            raise ValueError(
                f"COOKIE_SECURE must be true when ENVIRONMENT is {self.environment!r}"
            )
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")
        return self
{%- endif %}
{%- endif %}


@lru_cache
def get_settings() -> Settings:
{%- if auth %}
    # Required fields (secret_key) come from the environment, which mypy cannot see.
    return Settings()  # type: ignore[call-arg]
{%- else %}
    return Settings()
{%- endif %}
