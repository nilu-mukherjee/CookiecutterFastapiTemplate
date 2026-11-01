import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Entity


class SocialAccount(Entity):
    """A Google/GitHub account linked to a user (it belongs to the user).

    A provider account links to exactly one user, and a user has at most one account
    per provider.
    """

    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_social_accounts_provider_subject"),
        UniqueConstraint("user_id", "provider", name="uq_social_accounts_user_id_provider"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20))
    # The provider's stable account id (Google ``sub``, GitHub numeric id).
    subject: Mapped[str] = mapped_column(String(255))
    # The verified email the account had when it was linked (informational only).
    email: Mapped[str] = mapped_column(String(320))
