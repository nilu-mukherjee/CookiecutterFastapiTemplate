from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.models.item import Item
from app.repositories.base import BaseRepository


class ItemRepository(BaseRepository[Item]):
    model = Item

    async def list_after(
        self, limit: int, after: tuple[datetime, UUID] | None
    ) -> Sequence[Item]:
        stmt = select(Item).order_by(Item.created_at, Item.id).limit(limit)
        if after is not None:
            created_at, last_id = after
            stmt = stmt.where(
                or_(
                    Item.created_at > created_at,
                    and_(Item.created_at == created_at, Item.id > last_id),
                )
            )
        return (await self.session.scalars(stmt)).all()
