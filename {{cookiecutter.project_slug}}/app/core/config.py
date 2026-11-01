{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set session_auth = auth and cookiecutter.use_auth_session == "y" -%}
{%- set social = auth and cookiecutter.use_social_login == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{#- Cookie attributes and their safety checks apply to every cookie-based method, and to
    social login (its short-lived OAuth state cookie). -#}
{%- set cookie_auth = auth and (cookiecutter.use_auth_jwt_cookie == "y" or session_auth or social) -%}
{%- set use_jwt = auth and (cookiecutter.use_auth_jwt_bearer == "y" or cookiecutter.use_auth_jwt_cookie == "y") -%}
{%- set sse = cookiecutter.use_sse == "y" -%}
{%- set poll = cookiecutter.use_long_polling == "y" -%}
{%- set realtime = sse or poll or cookiecutter.use_websocket == "y" -%}
{%- set pydantic_names = [] -%}
{%- if sse or poll %}{% set _ = pydantic_names.append("PositiveFloat") %}{% endif -%}
{%- if realtime %}{% set _ = pydantic_names.append("PositiveInt") %}{% endif -%}
{%- if social %}{% set _ = pydantic_names.append("SecretStr") %}{% endif -%}
{%- if cookie_auth %}{% set _ = pydantic_names.append("field_validator") %}{% endif -%}
{%- if auth %}{% set _ = pydantic_names.append("model_validator") %}{% endif -%}
from functools import lru_cache
from typing import Literal

{% if pydantic_names %}from pydantic import {{ pydantic_names | join(", ") }}
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
{%- if realtime %}
    # Most long-lived connections (streams, long polls) one process serves at once.
    realtime_max_connections: PositiveInt = 1000
{%- endif %}
{%- if sse %}
    # Idle SSE streams send a comment this often so proxies keep the connection open.
    sse_heartbeat_seconds: PositiveFloat = 15.0
{%- endif %}
{%- if poll %}
    # Upper bound of a long poll's ``timeout`` (a larger value is clamped to it).
    long_poll_max_seconds: PositiveFloat = 30.0
{%- endif %}
{%- if cookiecutter.use_celery == "y" %}
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
{%- if cookiecutter.use_redis == "y" %}
    celery_result_backend: str = "redis://localhost:6379/1"
{%- else %}
    celery_result_backend: str = "rpc://"
{%- endif %}
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
{%- if session_auth %}
    # Sliding idle window and absolute cap of a login session.
    session_idle_timeout_minutes: int = 30
    session_absolute_lifetime_hours: int = 12
{%- endif %}
{%- if cookie_auth %}
{%- if social %}
    # Attributes of the auth cookies and of the OAuth state cookie. Secure is mandatory
    # outside dev (see below); Domain is only sent when set (host-only by default).
{%- else %}
    # Attributes of the auth cookies. Secure is mandatory outside dev (see below);
    # Domain is only sent when set (host-only cookies by default).
{%- endif %}
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
{%- endif %}
{%- if social %}
    # Social login. A provider is enabled only when both its id and secret are set;
    # the secrets are SecretStr so they never appear in a repr, log line or error.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    # The callback is <base>/api/v1/oauth/<provider>/callback and must be registered
    # with the provider verbatim. Never derived from the request's Host header.
    oauth_redirect_base_url: str = "http://localhost:8000"
    # Where a browser lands after a cookie/session social login. A constant: never
    # taken from the request, so the callback cannot be turned into an open redirect.
    oauth_success_redirect_url: str = "/"
{%- if not tenancy %}
    # Whether social login may create a new user for an unknown verified email.
    social_allow_signup: bool = False
{%- endif %}
{%- endif %}
{%- if cookie_auth %}

    @field_validator("cookie_domain", mode="before")
    @classmethod
    def _empty_domain_is_none(cls, value: object) -> object:
        # COOKIE_DOMAIN= (empty) means "no Domain attribute", not Domain="".
        return value or None
{%- endif %}
{%- if social %}

    @field_validator(
        "google_client_id",
        "google_client_secret",
        "github_client_id",
        "github_client_secret",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # GOOGLE_CLIENT_ID= (empty, as in .env.example) means "not configured".
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
