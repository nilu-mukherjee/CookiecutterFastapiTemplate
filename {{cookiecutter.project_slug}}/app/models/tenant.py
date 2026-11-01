{%- set social = cookiecutter.use_social_login == "y" -%}
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Entity

# Created at startup; the bootstrap superuser (a platform operator) lives in it.
SYSTEM_TENANT_SLUG = "system"
# Lowercase letters and digits, inner hyphens allowed, 3-40 characters.
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$"


class Tenant(Entity):
    """An isolated customer: every tenant-owned row (TenantMixin) points at one.

    Tenants are never deleted, only deactivated: a user of an inactive tenant cannot
    authenticate with any method.
    """

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
{%- if social %}
    # Whether a new user may join this tenant through social login (as a member).
    allow_social_signup: Mapped[bool] = mapped_column(Boolean, default=False)
{%- endif %}
