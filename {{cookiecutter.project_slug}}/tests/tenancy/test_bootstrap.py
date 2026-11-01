{%- set rbac = cookiecutter.use_rbac == "y" -%}
"""Startup: the system tenant exists exactly once and the first superuser lives in it."""
from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.main as main_module
from app.core.config import get_settings
from app.models import {% if rbac %}Role, {% endif %}Tenant, User
from app.models.tenant import SYSTEM_TENANT_SLUG
from app.services.tenant import TenantService


async def system_tenants(maker: async_sessionmaker[AsyncSession]) -> list[Tenant]:
    async with maker() as session:
        stmt = select(Tenant).where(Tenant.slug == SYSTEM_TENANT_SLUG)
        return list((await session.scalars(stmt)).all())


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_ensure_system_tenant_is_idempotent(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        ids = {(await TenantService(session).ensure_system_tenant()).id for _ in range(3)}
    assert len(ids) == 1
    assert [t.id for t in await system_tenants(maker)] == list(ids)


async def test_concurrent_system_tenant_loser_is_noop(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two workers starting together: the loser hits the unique slug, then reads the row."""
    winner = (await system_tenants(maker))[0]  # created by the maker fixture
    async with maker() as loser_session:
        loser = TenantService(loser_session)
        real = loser.repo.get_by_slug
        calls = {"n": 0}

        async def stale_once(slug: str) -> Tenant | None:
            calls["n"] += 1
            return None if calls["n"] == 1 else await real(slug)

        monkeypatch.setattr(loser.repo, "get_by_slug", stale_once)
        found = await loser.ensure_system_tenant()  # must not raise
    assert found.id == winner.id
    assert len(await system_tenants(maker)) == 1


async def test_lifespan_creates_the_superuser_in_the_system_tenant(
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    monkeypatch.setenv("FIRST_SUPERUSER_EMAIL", "root@example.com")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "bootstrap-password")
    get_settings.cache_clear()
    monkeypatch.setattr(main_module, "get_sessionmaker", lambda: maker)
    app = main_module.create_app()
    for _ in range(2):  # restarts are no-ops
        async with main_module.lifespan(app):
            pass
    [system] = await system_tenants(maker)
    async with maker() as session:
        users = (await session.scalars(select(User))).all()
        assert [(u.email, u.is_superuser, u.tenant_id) for u in users] == [
            ("root@example.com", True, system.id)
        ]
{%- if rbac %}
        roles = (await session.scalars(select(Role.name).where(Role.tenant_id == system.id))).all()
        assert sorted(roles) == ["admin", "member"]
{%- endif %}
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 1
