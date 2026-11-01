from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update

from app.models.session import Session
from app.repositories.base import BaseRepository


class SessionRepository(BaseRepository[Session]):
    model = Session

    async def get_by_hash(self, token_hash: str) -> Session | None:
        stmt = select(Session).where(Session.token_hash == token_hash)
        return (await self.session.scalars(stmt)).one_or_none()

    async def delete_by_hash(self, token_hash: str) -> None:
        stmt = (
            delete(Session)
            .where(Session.token_hash == token_hash)
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(stmt)

    async def set_last_seen(self, session_id: UUID, when: datetime) -> None:
        stmt = (
            update(Session)
            .where(Session.id == session_id)
            .values(last_seen_at=when)
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(stmt)
