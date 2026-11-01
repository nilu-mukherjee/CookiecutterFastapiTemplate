{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import {% if tenancy %}TenantEntity{% else %}Entity{% endif %}


class User({% if tenancy %}TenantEntity{% else %}Entity{% endif %}):
    __tablename__ = "users"

    # Always stored lowercased (UserService normalises), so equality is case-insensitive.
{%- if tenancy %}
    # Unique across ALL tenants: login is by email alone, the tenant follows from the user.
{%- endif %}
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
