{%- set social = cookiecutter.use_social_login == "y" -%}
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from app.models.tenant import SLUG_PATTERN

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class TenantCreate(BaseModel):
    """A new tenant and its first admin user, created together (all or nothing)."""

    name: Name
    # Permanent: used as the tenant's stable handle, never changed afterwards.
    slug: Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
    admin_email: EmailStr
    admin_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
{%- if social %}
    allow_social_signup: bool = False
{%- endif %}


class TenantUpdate(BaseModel):
    """Omitted fields are left unchanged; the slug cannot be changed."""

    name: Name | None = None
    is_active: bool | None = None
{%- if social %}
    allow_social_signup: bool | None = None
{%- endif %}

    @field_validator("name", "is_active"{% if social %}, "allow_social_signup"{% endif %})
    @classmethod
    def _not_null(cls, value: object) -> object:
        # The columns are NOT NULL: reject an explicit null, allow omission.
        if value is None:
            raise ValueError("must not be null")
        return value


class TenantRead(BaseModel):
    """A tenant as the platform operator sees it: no user data."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    is_active: bool
{%- if social %}
    allow_social_signup: bool
{%- endif %}
    created_at: datetime


class TenantList(BaseModel):
    items: list[TenantRead]
    total: int
    limit: int
    offset: int
