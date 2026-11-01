{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set redis = cookiecutter.use_redis == "y" -%}
{% if auth %}import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.main as main
{% endif %}from app.main import create_app
{% if redis %}from app.realtime.redis_broker import RedisBroker
{% else %}from app.realtime.broker import InMemoryBroker
{% endif %}

{% if auth %}async def test_lifespan_creates_the_broker(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lifespan's bootstrap runs against the test database.
    monkeypatch.setattr(main, "get_sessionmaker", lambda: maker)
{% else %}async def test_lifespan_creates_the_broker() -> None:
{% endif %}    application = create_app()
    async with application.router.lifespan_context(application):
{%- if redis %}
        # Redis Streams, so every worker process shares one stream.
        assert isinstance(application.state.broker, RedisBroker)
{%- else %}
        assert isinstance(application.state.broker, InMemoryBroker)
{%- endif %}
