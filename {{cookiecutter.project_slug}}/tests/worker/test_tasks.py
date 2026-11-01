{%- set redis = cookiecutter.use_redis == "y" -%}
{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID{% if tenancy %}, uuid4{% endif %}

{% if tenancy %}import pytest

{% endif %}from app.core.config import get_settings
from app.worker.celery_app import celery_app, create_celery_app
from app.worker.tasks.example import example_task

PROJECT_ROOT = Path(__file__).resolve().parents[2]


{% if tenancy %}def test_example_task_returns_payload_with_tenant() -> None:
    tenant_id = str(uuid4())
    result = example_task.apply_async(
        kwargs={"tenant_id": tenant_id, "payload": {"item_id": "x"}}
    ).get()
    assert result == {"tenant_id": tenant_id, "payload": {"item_id": "x"}, "processed": True}


def test_example_task_requires_a_tenant_id() -> None:
    with pytest.raises(TypeError):
        example_task.apply_async(kwargs={"payload": {}}).get()
{% else %}def test_example_task_returns_payload() -> None:
    result = example_task.apply_async(kwargs={"payload": {"item_id": "x"}}).get()
    assert result == {"payload": {"item_id": "x"}, "processed": True}
{% endif %}

def test_example_task_has_a_fixed_name() -> None:
    assert example_task.name == "app.worker.tasks.example.example_task"
    assert example_task.name in celery_app.tasks


def test_task_modules_are_included_for_the_worker() -> None:
    assert "app.worker.tasks.example" in celery_app.conf.include


def test_result_backend_matches_the_build() -> None:
{%- if redis %}
    assert celery_app.conf.result_backend == "redis://localhost:6379/1"
{%- else %}
    assert celery_app.conf.result_backend == "rpc://"
{%- endif %}
    assert celery_app.conf.result_backend == get_settings().celery_result_backend


def test_broker_url_comes_from_settings() -> None:
    settings = get_settings().model_copy(update={"celery_broker_url": "amqp://u:p@broker:5672//"})
    assert create_celery_app(settings).conf.broker_url == "amqp://u:p@broker:5672//"


def test_serialisation_is_json_only() -> None:
    conf = celery_app.conf
    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert list(conf.accept_content) == ["json"]
    # A pickled message (code execution on the worker) must never be accepted.
    assert "pickle" not in conf.accept_content
    assert "application/x-python-serialize" not in conf.accept_content


def test_reliability_and_limits_are_configured() -> None:
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_time_limit == 300
    assert conf.task_soft_time_limit == 240
    assert conf.result_expires == 86400
    assert conf.broker_connection_retry_on_startup is True
    assert conf.broker_connection_timeout == 2
    assert conf.timezone == "UTC"
    assert conf.enable_utc is True


def test_importing_the_app_does_not_connect_to_the_broker() -> None:
    # An unroutable broker: a connection attempt at import time would hang or fail.
    env = {**os.environ, "CELERY_BROKER_URL": "amqp://guest:guest@10.255.255.1:5672//"}
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c", "import app.worker.celery_app"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert time.monotonic() - started < 30


def test_task_ids_are_uuids() -> None:
    result = example_task.apply_async(
        kwargs={% if tenancy %}{"tenant_id": str(uuid4()), "payload": {}}{% else %}{"payload": {}}{% endif %}
    )
    assert str(UUID(result.id)) == result.id
