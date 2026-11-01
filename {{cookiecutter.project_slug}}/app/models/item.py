{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import {% if tenancy %}TenantEntity{% else %}Entity{% endif %}


class Item({% if tenancy %}TenantEntity{% else %}Entity{% endif %}):
    __tablename__ = "items"

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
