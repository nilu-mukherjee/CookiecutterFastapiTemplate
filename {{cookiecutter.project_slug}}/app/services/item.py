import base64
import binascii
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.item import Item
from app.repositories.item import ItemRepository


def encode_cursor(item: Item) -> str:
    raw = f"{item.created_at.isoformat()}|{item.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, _, ident = raw.partition("|")
        return datetime.fromisoformat(ts), UUID(ident)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise AppError(400, "invalid_cursor", "Invalid pagination cursor") from exc


class ItemService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ItemRepository(session)

    async def create(self, name: str, description: str | None = None) -> Item:
        item = await self.repo.add(Item(name=name, description=description))
        await self.session.commit()
        return item

    async def get(self, item_id: UUID) -> Item:
        item = await self.repo.get(item_id)
        if item is None:
            raise AppError(404, "not_found", "Item not found")
        return item

    async def list(self, limit: int, offset: int) -> tuple[Sequence[Item], int]:
        return await self.repo.list(limit, offset), await self.repo.count()

    async def list_cursor(
        self, limit: int, cursor: str | None
    ) -> tuple[Sequence[Item], str | None]:
        after = decode_cursor(cursor) if cursor else None
        rows = await self.repo.list_after(limit + 1, after)
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1]) if len(rows) > limit else None
        return page, next_cursor

    async def update(self, item_id: UUID, changes: dict[str, Any]) -> Item:
        item = await self.get(item_id)
        for key, value in changes.items():
            setattr(item, key, value)
        await self.session.commit()
        return item

    async def delete(self, item_id: UUID) -> None:
        item = await self.get(item_id)
        await self.repo.delete(item)
        await self.session.commit()
