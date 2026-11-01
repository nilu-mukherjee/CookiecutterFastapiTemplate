from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update

from app.models.api_key import ApiKey
from app.repositories.base import BaseRepository


class ApiKeyRepository(BaseRepository[ApiKey]):
    model = ApiKey

    async def get_by_prefix(self, prefix: str) -> ApiKey | None:
        stmt = select(ApiKey).where(ApiKey.prefix == prefix)
        return (await self.session.scalars(stmt)).one_or_none()

    async def get_for_user(self, user_id: UUID, key_id: UUID) -> ApiKey | None:
        """A user's own key; someone else's id looks exactly like a missing one."""
        stmt = select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
        return (await self.session.scalars(stmt)).one_or_none()

    async def list_for_user(self, user_id: UUID, limit: int, offset: int) -> list[ApiKey]:
        stmt = (
            select(ApiKey)
            .where(ApiKey.user_id == user_id)
            .order_by(ApiKey.created_at, ApiKey.id)
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.scalars(stmt)).all())

    async def count_for_user(self, user_id: UUID) -> int:
        stmt = select(func.count()).select_from(ApiKey).where(ApiKey.user_id == user_id)
        return await self.session.scalar(stmt) or 0

    async def count_active(self, user_id: UUID, now: datetime) -> int:
        """Keys that are neither revoked nor expired."""
        stmt = (
            select(func.count())
            .select_from(ApiKey)
            .where(
                ApiKey.user_id == user_id,
                ApiKey.revoked_at.is_(None),
                or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
            )
        )
        return await self.session.scalar(stmt) or 0

    async def set_last_used(self, key_id: UUID, when: datetime) -> None:
        stmt = (
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(last_used_at=when)
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(stmt)
