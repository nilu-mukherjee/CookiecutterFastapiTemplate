{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
{%- set realtime = cookiecutter.use_sse == "y" or cookiecutter.use_long_polling == "y" or cookiecutter.use_websocket == "y" -%}
import base64
import binascii
{% if realtime %}import logging
{% endif %}from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.item import Item
{% if realtime %}from app.realtime.broker import EventBroker, channel_for
{% endif %}from app.repositories.item import ItemRepository
{%- if realtime %}

logger = logging.getLogger(__name__)
{%- endif %}


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
{%- if tenancy and realtime %}
    """Items of ONE tenant: the caller's (``Principal.tenant_id``).

    Writes publish ``item.<created|updated|deleted>`` events to that tenant's channel.
    """

    def __init__(self, session: AsyncSession, tenant_id: UUID, broker: EventBroker) -> None:
        self.session = session
        self.repo = ItemRepository(session, tenant_id)
        self.broker = broker
        self.channel = channel_for(tenant_id)
{%- elif tenancy %}
    """Items of ONE tenant: the caller's (``Principal.tenant_id``)."""

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self.session = session
        self.repo = ItemRepository(session, tenant_id)
{%- elif realtime %}
    """Items. Writes publish ``item.<created|updated|deleted>`` events."""

    def __init__(self, session: AsyncSession, broker: EventBroker) -> None:
        self.session = session
        self.repo = ItemRepository(session)
        self.broker = broker
        self.channel = channel_for(None)
{%- else %}
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ItemRepository(session)
{%- endif %}

    async def create(self, name: str, description: str | None = None) -> Item:
        item = await self.repo.add(Item(name=name, description=description))
        await self.session.commit()
{%- if realtime %}
        await self._publish("item.created", item.id)
{%- endif %}
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
{%- if realtime %}
        await self._publish("item.updated", item.id)
{%- endif %}
        return item

    async def delete(self, item_id: UUID) -> None:
        item = await self.get(item_id)
        await self.repo.delete(item)
        await self.session.commit()
{%- if realtime %}
        await self._publish("item.deleted", item_id)

    async def _publish(self, event_type: str, item_id: UUID) -> None:
        """Best effort, after the commit: the write has happened whatever the broker does.

        The event carries the id only; subscribers re-fetch the item through the API.
        """
        try:
            await self.broker.publish(self.channel, event_type, {"id": str(item_id)})
        except Exception as exc:
            # The class only: a message can contain the broker URL and credentials.
            logger.warning("item event not published: %s", type(exc).__name__)
{%- endif %}
