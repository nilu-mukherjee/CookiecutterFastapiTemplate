{%- set beat = cookiecutter.use_celery_beat == "y" -%}
{% if beat %}import logging
{% endif %}from typing import Any

from app.worker.celery_app import celery_app
{% if beat %}
logger = logging.getLogger(__name__)

{% endif %}

@celery_app.task(name="app.worker.tasks.example.example_task")
def example_task(tenant_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Example task. Arguments and result must be JSON-serialisable.

    ``tenant_id`` is passed explicitly by the caller (a UUID string, or None); a task
    never reads tenant context from globals.
    """
    return {"tenant_id": tenant_id, "payload": payload, "processed": True}
{%- if beat %}


@celery_app.task(name="app.worker.tasks.example.periodic_example")
def periodic_example() -> dict[str, bool]:
    """Runs every 60 seconds when ``celery beat`` is running."""
    logger.info("periodic example task ran")
    return {"ok": True}
{%- endif %}
