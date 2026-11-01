from uuid import UUID

from sqlalchemy import select

from app.models.social_account import SocialAccount
from app.repositories.base import BaseRepository


class SocialAccountRepository(BaseRepository[SocialAccount]):
    """Linked provider accounts. Lookups are by the provider's identity, before any user
    is known (social login is an authentication stage)."""

    model = SocialAccount

    async def get_by_subject(self, provider: str, subject: str) -> SocialAccount | None:
        stmt = select(SocialAccount).where(
            SocialAccount.provider == provider, SocialAccount.subject == subject
        )
        return (await self.session.scalars(stmt)).one_or_none()

    async def get_for_user(self, user_id: UUID, provider: str) -> SocialAccount | None:
        stmt = select(SocialAccount).where(
            SocialAccount.user_id == user_id, SocialAccount.provider == provider
        )
        return (await self.session.scalars(stmt)).one_or_none()
