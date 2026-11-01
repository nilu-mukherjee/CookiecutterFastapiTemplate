{%- set beat = cookiecutter.use_celery_beat == "y" -%}
from celery import Celery

from app.core.config import Settings, get_settings


def create_celery_app(settings: Settings) -> Celery:
    """Build the Celery application. Creating it never connects to the broker."""
    app = Celery(
        "{{ cookiecutter.project_slug }}",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["app.worker.tasks.example"],
    )
    app.conf.update(
        # JSON only: a pickled message would execute code on the worker.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Acknowledge after the task ran, one task at a time per process: a crashed
        # worker does not lose its task, and long tasks do not starve other workers.
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_time_limit=300,
        task_soft_time_limit=240,
        result_expires=86400,
        broker_connection_retry_on_startup=True,
        # Fail fast when the broker is down (the API answers 503 instead of hanging).
        broker_connection_timeout=2,
        timezone="UTC",
        enable_utc=True,
    )
{%- if beat %}
    app.conf.beat_schedule = {
        "periodic-example": {
            "task": "app.worker.tasks.example.periodic_example",
            "schedule": 60.0,
        },
    }
{%- endif %}
    return app


celery_app = create_celery_app(get_settings())
