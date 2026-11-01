{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
from sqlalchemy import Column, ForeignKey, String, Table, {% if tenancy %}UniqueConstraint, {% endif %}Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, {% if tenancy %}TenantEntity{% else %}Entity{% endif %}
from app.models.permission import Permission

# Composite primary keys already index the leading column; the other one is indexed here.
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        Uuid,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "role_id",
        Uuid,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class Role({% if tenancy %}TenantEntity{% else %}Entity{% endif %}):
    __tablename__ = "roles"
{%- if tenancy %}
    # Role names are unique per tenant: every tenant has its own admin and member.
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_id_name"),)

    name: Mapped[str] = mapped_column(String(100))
{%- else %}

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
{%- endif %}
    # Never lazy-loaded (async): repositories load it explicitly with selectinload.
    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions, lazy="raise")
