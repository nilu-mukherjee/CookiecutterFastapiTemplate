from collections.abc import Iterator

import pytest

from app.worker.celery_app import celery_app


@pytest.fixture(autouse=True)
def eager_celery() -> Iterator[None]:
    """Run tasks in-process: no broker, no result backend, no network."""
    before = (celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates = before
