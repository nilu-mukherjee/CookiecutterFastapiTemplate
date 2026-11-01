import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.core.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TABLES = {
    "alembic_version",
    "items",
{%- if cookiecutter.use_auth == "y" %}
    "users",
{%- endif %}
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_auth_jwt_bearer == "y" %}
    "refresh_tokens",
{%- endif %}
}


@pytest.fixture
def sqlite_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db = tmp_path / "migrations.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    get_settings.cache_clear()
    yield db
    get_settings.cache_clear()


def _config() -> Config:
    # No ini file: fileConfig() would replace the test run's logging handlers.
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def _tables(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def test_upgrade_head_and_downgrade_base_on_sqlite(sqlite_url: Path) -> None:
    command.upgrade(_config(), "head")
    assert _tables(sqlite_url) == EXPECTED_TABLES
    command.downgrade(_config(), "base")
    assert _tables(sqlite_url) == {"alembic_version"}
