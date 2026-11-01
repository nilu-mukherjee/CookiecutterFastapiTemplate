import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TABLES = {
    "alembic_version",
    "items",
{%- if cookiecutter.use_auth == "y" %}
    "users",
{%- endif %}
{%- if cookiecutter.use_auth == "y" and (cookiecutter.use_auth_jwt_bearer == "y" or cookiecutter.use_auth_jwt_cookie == "y") %}
    "refresh_tokens",
{%- endif %}
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_auth_session == "y" %}
    "sessions",
{%- endif %}
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_auth_api_key == "y" %}
    "api_keys",
{%- endif %}
{%- if cookiecutter.use_auth == "y" and cookiecutter.use_rbac == "y" %}
    "permissions",
    "roles",
    "role_permissions",
    "user_roles",
{%- endif %}
}
# The newest revision that exists in this build (the chain skips pruned ones).
EXPECTED_HEAD = "{{ '0005' if cookiecutter.use_auth == 'y' and cookiecutter.use_rbac == 'y' else '0004' if cookiecutter.use_auth == 'y' and cookiecutter.use_auth_api_key == 'y' else ('0003' if cookiecutter.use_auth == 'y' and cookiecutter.use_auth_session == 'y' else ('0002' if cookiecutter.use_auth == 'y' else '0001')) }}"


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


def test_migration_history_is_linear_with_a_single_head() -> None:
    script = ScriptDirectory.from_config(_config())
    assert len(script.get_heads()) == 1
    # Every revision has exactly one parent: no branches, no merges.
    for rev in script.walk_revisions():
        assert not isinstance(rev.down_revision, tuple), rev.revision


def test_revisions_form_one_unbroken_chain_ending_at_the_expected_head() -> None:
    script = ScriptDirectory.from_config(_config())
    assert script.get_heads() == [EXPECTED_HEAD]
    # Walking from the head must reach the base without a gap: each down_revision exists.
    chain = [rev.revision for rev in script.walk_revisions()]
    assert chain[0] == EXPECTED_HEAD
    assert chain[-1] == "0001"
    assert len(chain) == len(set(chain))
    assert len(chain) == len(list(script.walk_revisions()))
