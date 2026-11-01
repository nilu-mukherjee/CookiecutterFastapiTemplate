from datetime import datetime
from uuid import UUID

from sqlalchemy import CursorResult, select, update

from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return (await self.session.scalars(stmt)).one_or_none()

    async def revoke_if_active(self, token_hash: str, now: datetime) -> bool:
        """Atomically revoke a live token; True only for the caller that revoked it.

        One conditional UPDATE, so two concurrent refreshes with the same token
        cannot both succeed: the database lets exactly one change the row.
        """
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        assert isinstance(result, CursorResult)
        return result.rowcount == 1

    async def revoke(self, token_hash: str, now: datetime) -> None:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(stmt)

    async def revoke_all_for_user(self, user_id: UUID, now: datetime) -> None:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(stmt)
