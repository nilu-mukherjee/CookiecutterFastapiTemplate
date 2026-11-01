import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Entity


class Session(Entity):
    """A server-side login session. Only the SHA-256 of the cookie's session id is stored."""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Absolute lifetime: activity never moves this.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
