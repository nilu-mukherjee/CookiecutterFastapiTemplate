from typing import Annotated

from fastapi import Depends
{% if cookiecutter.use_redis == "y" %}from redis.asyncio import Redis
{% endif %}from sqlalchemy.ext.asyncio import AsyncSession

{% if cookiecutter.use_redis == "y" %}from app.cache.redis import get_redis
{% endif %}from app.db.session import get_session
from app.services.item import ItemService

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_item_service(session: SessionDep) -> ItemService:
    return ItemService(session)


ItemServiceDep = Annotated[ItemService, Depends(get_item_service)]
{%- if cookiecutter.use_redis == "y" %}

RedisDep = Annotated[Redis, Depends(get_redis)]
{%- endif %}
