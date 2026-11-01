{%- set redis = cookiecutter.use_redis == "y" -%}
"""Realtime item events (``broker.py`` defines the contract)."""
{% if redis %}from app.cache.redis import get_redis, get_stream_redis
from app.realtime.broker import EventBroker
from app.realtime.redis_broker import RedisBroker
{% else %}from app.realtime.broker import EventBroker, InMemoryBroker
{% endif %}

def create_broker() -> EventBroker:
    """The process's broker, created once at startup (lifespan) as ``app.state.broker``."""
{%- if redis %}
    # Redis Streams: shared by every worker process and replayable after a restart.
    return RedisBroker(get_redis(), blocking_client=get_stream_redis())
{%- else %}
    # In-process: events reach only clients of this process (run one worker, or enable
    # Redis when generating the project for a shared stream).
    return InMemoryBroker()
{%- endif %}
