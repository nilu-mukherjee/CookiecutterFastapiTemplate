from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Entity


class Permission(Entity):
    """A named capability such as ``items:write``; roles hold sets of them."""

    __tablename__ = "permissions"

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
