import uuid
from collections.abc import Sequence
from typing import Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Entity

T = TypeVar("T", bound=Entity)


class BaseRepository(Generic[T]):  # noqa: UP046
    model: type[T]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id: uuid.UUID) -> T | None:
        return await self.session.get(self.model, id)

    async def list(self, limit: int, offset: int) -> Sequence[T]:
        stmt = (
            select(self.model)
            .order_by(self.model.created_at, self.model.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def count(self) -> int:
        result = await self.session.scalar(select(func.count()).select_from(self.model))
        return result or 0

    async def add(self, obj: T) -> T:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete(self, obj: T) -> None:
        await self.session.delete(obj)
        await self.session.flush()
