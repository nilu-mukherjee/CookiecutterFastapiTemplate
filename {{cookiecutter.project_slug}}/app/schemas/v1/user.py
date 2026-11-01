{%- set rbac = cookiecutter.use_rbac == "y" -%}
{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    is_superuser: bool = False
{%- if rbac %}
    # Role names to grant; ``[]`` creates a user with no permissions.
    roles: list[str] = Field(default_factory=lambda: ["member"], max_length=50)
{%- endif %}


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    is_active: bool
    is_superuser: bool
    created_at: datetime
{%- if tenancy %}
    tenant_id: UUID
{%- endif %}
{%- if rbac %}
    # Filled by the endpoints (roles live in their own table).
    roles: list[str] = Field(default_factory=list)
{%- endif %}


class UserList(BaseModel):
    items: list[UserRead]
    total: int
    limit: int
    offset: int
