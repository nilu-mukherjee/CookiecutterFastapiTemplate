{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
{%- set social = cookiecutter.use_auth == "y" and cookiecutter.use_social_login == "y" -%}
{%- set inspect_schema = tenancy or social -%}
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.models import Base

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TABLES = {
    "alembic_version",
    "items",
{%- if tenancy %}
    "tenants",
{%- endif %}
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
{%- if social %}
    "social_accounts",
{%- endif %}
}
{%- if tenancy %}
# Tenant-owned tables: each has a non-null, indexed tenant_id referencing tenants.id.
TENANT_OWNED = [
    "items",
    "users",
{%- if cookiecutter.use_rbac == "y" %}
    "roles",
{%- endif %}
]
{%- endif %}
# The newest revision that exists in this build (the chain skips pruned ones).
EXPECTED_HEAD = "{{ '0006' if cookiecutter.use_auth == 'y' and cookiecutter.use_social_login == 'y' else '0005' if cookiecutter.use_auth == 'y' and cookiecutter.use_rbac == 'y' else '0004' if cookiecutter.use_auth == 'y' and cookiecutter.use_auth_api_key == 'y' else ('0003' if cookiecutter.use_auth == 'y' and cookiecutter.use_auth_session == 'y' else ('0002' if cookiecutter.use_auth == 'y' else '0001')) }}"


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


{% if inspect_schema %}def _columns(db: Path, table: str) -> dict[str, bool]:
    """Column name -> NOT NULL."""
    with sqlite3.connect(db) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: bool(r[3]) for r in rows}


def _indexed_columns(db: Path, table: str) -> list[tuple[list[str], bool]]:
    """(columns, unique) of every index on the table, constraint-backed ones included."""
    with sqlite3.connect(db) as conn:
        indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
        return [
            ([c[2] for c in conn.execute(f"PRAGMA index_info('{ix[1]}')").fetchall()], bool(ix[2]))
            for ix in indexes
        ]


def _foreign_keys(db: Path, table: str) -> set[tuple[str, str, str]]:
    """(column, referenced table, referenced column)."""
    with sqlite3.connect(db) as conn:
        rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    return {(r[3], r[2], r[4]) for r in rows}


{% endif %}def _tables(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def test_upgrade_head_and_downgrade_base_on_sqlite(sqlite_url: Path) -> None:
    command.upgrade(_config(), "head")
    assert _tables(sqlite_url) == EXPECTED_TABLES
    command.downgrade(_config(), "base")
    assert _tables(sqlite_url) == {"alembic_version"}
{%- if tenancy %}


def test_tenant_owned_tables_have_a_required_indexed_tenant_fk(sqlite_url: Path) -> None:
    command.upgrade(_config(), "head")
    assert {"id", "name", "slug", "is_active"} <= set(_columns(sqlite_url, "tenants"))
    assert (["slug"], True) in _indexed_columns(sqlite_url, "tenants")
    for table in TENANT_OWNED:
        assert _columns(sqlite_url, table).get("tenant_id") is True, table
        assert (["tenant_id"], False) in _indexed_columns(sqlite_url, table), table
        assert ("tenant_id", "tenants", "id") in _foreign_keys(sqlite_url, table), table
    # Email stays globally unique: login is by email alone.
    assert (["email"], True) in _indexed_columns(sqlite_url, "users")
{%- if cookiecutter.use_rbac == "y" %}
    # Role names are unique per tenant only.
    role_indexes = _indexed_columns(sqlite_url, "roles")
    assert (["tenant_id", "name"], True) in role_indexes
    assert (["name"], True) not in role_indexes
{%- endif %}
    command.downgrade(_config(), "base")
    assert _tables(sqlite_url) == {"alembic_version"}
{%- endif %}


{%- if social %}


def test_social_accounts_belong_to_users_with_unique_provider_identities(
    sqlite_url: Path,
) -> None:
    command.upgrade(_config(), "head")
    columns = _columns(sqlite_url, "social_accounts")
    assert {"id", "user_id", "provider", "subject", "email", "created_at"} <= set(columns)
    assert all(columns[c] for c in ("user_id", "provider", "subject", "email"))
    indexes = _indexed_columns(sqlite_url, "social_accounts")
    # A provider account links to one user; a user has one account per provider.
    assert (["provider", "subject"], True) in indexes
    assert (["user_id", "provider"], True) in indexes
    assert (["user_id"], False) in indexes
    assert ("user_id", "users", "id") in _foreign_keys(sqlite_url, "social_accounts")
    with sqlite3.connect(sqlite_url) as conn:
        rows = conn.execute("PRAGMA foreign_key_list(social_accounts)").fetchall()
    assert {r[6] for r in rows} == {"CASCADE"}  # on_delete
{%- if tenancy %}
    # Not tenant-owned: it belongs to the user (who belongs to a tenant).
    assert "tenant_id" not in columns
{%- endif %}
    command.downgrade(_config(), "base")
    assert _tables(sqlite_url) == {"alembic_version"}
{%- endif %}


def test_migrations_match_the_models(sqlite_url: Path) -> None:
    """``alembic upgrade head`` builds exactly the schema the models declare."""
    command.upgrade(_config(), "head")
    engine = create_engine(f"sqlite:///{sqlite_url}")
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


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
