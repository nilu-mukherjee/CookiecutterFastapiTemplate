{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
from collections.abc import Iterator
{% if tenancy %}from uuid import UUID
{% endif %}
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models import Permission, Role, User
from app.models.role import role_permissions
from app.services.rbac import RbacService
{% if tenancy %}from app.services.tenant import TenantService
{% endif %}from app.services.user import UserService
from tests.conftest import MakeUser

ALL = ["items:read", "items:write", "users:read", "users:write"]
MEMBER = ["items:read", "items:write"]
{%- macro rbac(s, t="await system_id(" ~ s ~ ")") %}RbacService({{ s }}{% if tenancy %}, {{ t }}{% endif %}){% endmacro %}
{%- if tenancy %}


async def system_id(session: AsyncSession) -> UUID:
    """Roles are per tenant: unless a test says otherwise it uses the system tenant's
    roles, seeded by the ``maker`` fixture as the lifespan does."""
    return (await TenantService(session).ensure_system_tenant()).id
{%- endif %}


async def role_map(session: AsyncSession) -> dict[str, list[str]]:
    roles = await {{ rbac("session") }}.list_roles()
    return {r.name: sorted(p.name for p in r.permissions) for r in roles}


async def count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_ensure_permissions_creates_exactly_the_four_names_once(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        service = {{ rbac("session") }}
        await service.ensure_permissions()
        await service.ensure_permissions()
        names = sorted((await session.scalars(select(Permission.name))).all())
    assert names == ALL


async def test_ensure_default_roles_is_idempotent(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        for _ in range(3):
            await {{ rbac("session") }}.ensure_default_roles()
        assert await count(session, Role) == 2
        assert await count(session, Permission) == 4
        assert await role_map(session) == {"admin": ALL, "member": MEMBER}


async def test_ensure_default_roles_does_not_overwrite_a_customised_role(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        member = (await session.scalars(select(Role).where(Role.name == "member"))).one()
        keep = (
            await session.scalars(select(Permission).where(Permission.name == "items:read"))
        ).one()
        await session.execute(
            delete(role_permissions).where(role_permissions.c.role_id == member.id)
        )
        await session.execute(
            role_permissions.insert().values(role_id=member.id, permission_id=keep.id)
        )
        await session.commit()
    async with maker() as session:
        await {{ rbac("session") }}.ensure_default_roles()
        assert (await role_map(session))["member"] == ["items:read"]


async def test_ensure_default_roles_recreates_a_deleted_role_with_defaults(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        member = (await session.scalars(select(Role).where(Role.name == "member"))).one()
        await session.execute(
            delete(role_permissions).where(role_permissions.c.role_id == member.id)
        )
        await session.execute(delete(Role).where(Role.id == member.id))
        await session.commit()
        await {{ rbac("session") }}.ensure_default_roles()
        assert await role_map(session) == {"admin": ALL, "member": MEMBER}


async def test_concurrent_permission_seed_loser_is_noop(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with maker() as session:
        await session.execute(delete(role_permissions))
        await session.execute(delete(Role))
        await session.execute(delete(Permission))
        await session.commit()
    async with maker() as winner_session, maker() as loser_session:
        loser = {{ rbac("loser_session") }}
        calls = {"n": 0}
        real = loser.permissions.get_by_names

        async def stale_once(names: list[str]) -> list[Permission]:
            calls["n"] += 1
            return [] if calls["n"] == 1 else list(await real(names))

        monkeypatch.setattr(loser.permissions, "get_by_names", stale_once)
        await {{ rbac("winner_session") }}.ensure_permissions()
        await loser.ensure_permissions()  # hits the unique index; must not raise
    async with maker() as session:
        assert sorted((await session.scalars(select(Permission.name))).all()) == ALL


async def test_concurrent_role_seed_loser_is_noop(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with maker() as loser_session:
        loser = {{ rbac("loser_session") }}

        async def not_found_yet(name: str) -> None:
            return None  # the loser checked before the winner committed

        monkeypatch.setattr(loser.roles, "get_by_name", not_found_yet)
        await loser.ensure_default_roles()  # hits the unique index; must not raise
    async with maker() as session:
        assert await role_map(session) == {"admin": ALL, "member": MEMBER}


async def test_grant_default_roles_gives_member(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    user = await make_user("none@example.com", roles=[])
    async with maker() as session:
        service = {{ rbac("session", "user.tenant_id") }}
        assert (await service.role_names([user.id])).get(user.id, []) == []
        await service.grant_default_roles(user.id)
        assert (await service.role_names([user.id]))[user.id] == ["member"]
        assert await service.permissions_for(user.id) == set(MEMBER)


async def test_resolve_roles_rejects_unknown_names(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        with pytest.raises(AppError) as exc:
            await {{ rbac("session") }}.resolve_roles(["member", "ghost"])
    assert exc.value.status_code == 422
    assert exc.value.code == "unknown_role"


async def test_creating_a_user_reseeds_missing_default_roles(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    async with maker() as session:
        await session.execute(delete(role_permissions))
        await session.execute(delete(Role))
        await session.commit()
    user = await make_user("late@example.com")
    async with maker() as session:
{%- if tenancy %}
        names = await RbacService(session, user.tenant_id).role_names([user.id])
        assert names[user.id] == ["member"]
{%- else %}
        assert (await RbacService(session).role_names([user.id]))[user.id] == ["member"]
{%- endif %}


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_bootstrap_superuser_gets_admin_role(
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    monkeypatch.setenv("FIRST_SUPERUSER_EMAIL", "boss@example.com")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "bootstrap-password")
    get_settings.cache_clear()
    async with maker() as session:
        created = await {% if tenancy %}UserService(session, await system_id(session)){% else %}UserService(session){% endif %}.bootstrap_superuser()
        assert created is not None
        user = (await session.scalars(select(User).where(User.id == created.id))).one()
{%- if tenancy %}
        names = await RbacService(session, user.tenant_id).role_names([user.id])
        assert names[user.id] == ["admin"]
{%- else %}
        assert (await RbacService(session).role_names([user.id]))[user.id] == ["admin"]
{%- endif %}
