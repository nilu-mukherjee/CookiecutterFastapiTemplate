import logging

import pytest

from app.worker.celery_app import celery_app
from app.worker.tasks.example import periodic_example


def test_beat_schedule_has_exactly_the_one_periodic_entry() -> None:
    schedule = celery_app.conf.beat_schedule
    assert list(schedule) == ["periodic-example"]
    entry = schedule["periodic-example"]
    assert entry["task"] == "app.worker.tasks.example.periodic_example"
    assert entry["schedule"] == 60.0
    assert entry["task"] in celery_app.tasks


def test_periodic_example_logs_and_returns_ok(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        assert periodic_example() == {"ok": True}
    records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(records) == 1
