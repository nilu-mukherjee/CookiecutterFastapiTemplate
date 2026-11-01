{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.core.security import MAX_PASSWORD_LENGTH


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
{%- if bearer and jwt_cookie %}
    # How the tokens are delivered: JSON body (bearer) or HttpOnly cookies.
    delivery: Literal["bearer", "cookie"] = "bearer"
{%- elif jwt_cookie %}
    # Only cookie delivery is enabled; "bearer" is rejected with 422.
    delivery: Literal["cookie"] = "cookie"
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
