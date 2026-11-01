{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
import uuid
from datetime import UTC, datetime

{% if tenancy %}from sqlalchemy import DateTime, ForeignKey, Uuid
{% else %}from sqlalchemy import DateTime, Uuid
{% endif %}from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Entity(Base):
    """Abstract base: UUID primary key and creation timestamp."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
{%- if tenancy %}


class TenantMixin:
    """A tenant-owned table: a non-null, indexed ``tenant_id``.

    Its repository must be a ``TenantScopedRepository`` (tests/tenancy enforces it), so
    no query on the table can forget the tenant filter.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), index=True)


class TenantEntity(TenantMixin, Entity):
    """Abstract base of tenant-owned models: Entity plus TenantMixin."""

    __abstract__ = True
{%- endif %}
