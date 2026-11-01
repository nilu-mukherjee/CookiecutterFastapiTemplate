{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{#- /login ``delivery`` values, in default order: the first enabled one is the default. -#}
{%- set deliveries = [] -%}
{%- if bearer %}{% set _ = deliveries.append("bearer") %}{% endif -%}
{%- if jwt_cookie %}{% set _ = deliveries.append("cookie") %}{% endif -%}
{%- if session %}{% set _ = deliveries.append("session") %}{% endif -%}
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.core.security import MAX_PASSWORD_LENGTH


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
{%- if deliveries | length > 1 %}
    # How the login is delivered: tokens in the JSON body (bearer), HttpOnly JWT
    # cookies (cookie) or a server-side session cookie (session). Disabled ones are 422.
    delivery: Literal[{% for d in deliveries %}"{{ d }}"{% if not loop.last %}, {% endif %}{% endfor %}] = "{{ deliveries[0] }}"
{%- elif deliveries and deliveries != ["bearer"] %}
    # Only {{ deliveries[0] }} delivery is enabled; the others are rejected with 422.
    delivery: Literal["{{ deliveries[0] }}"] = "{{ deliveries[0] }}"
{%- endif %}
{%- if bearer %}


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
{%- endif %}
{%- if jwt_cookie %}


class CookieSession(BaseModel):
    """Cookie delivery: the tokens are in HttpOnly cookies, never in the body."""

    token_type: Literal["cookie"] = "cookie"
    expires_in: int
    # Echo this in the X-CSRF-Token header on POST/PUT/PATCH/DELETE.
    csrf_token: str
{%- endif %}
{%- if session %}


class SessionLogin(BaseModel):
    """Session delivery: the session id is in an HttpOnly cookie, never in the body."""

    token_type: Literal["session"] = "session"
    # Seconds until the absolute session lifetime ends.
    expires_in: int
    # Echo this in the X-CSRF-Token header on POST/PUT/PATCH/DELETE.
    csrf_token: str
{%- endif %}
