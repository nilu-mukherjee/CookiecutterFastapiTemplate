"""RBAC under multi-tenancy: roles belong to a tenant, permissions are global."""
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Permission, Role, Tenant
from app.models.role import user_roles
from app.services.rbac import RbacService
from tests.conftest import MakeUser
from tests.tenancy.conftest import METHODS, MethodClient

USERS = "/api/v1/users"
ROLES = "/api/v1/roles"


async def add_role(
    maker: async_sessionmaker[AsyncSession], tenant: Tenant, name: str, permissions: list[str]
) -> Role:
    async with maker() as session:
        found = (
            await session.scalars(select(Permission).where(Permission.name.in_(permissions)))
        ).all()
        role = Role(name=name, permissions=list(found), tenant_id=tenant.id)
        session.add(role)
        await session.commit()
        return role


async def roles_of(maker: async_sessionmaker[AsyncSession], tenant: Tenant) -> dict[str, str]:
    async with maker() as session:
        rows = (await session.scalars(select(Role).where(Role.tenant_id == tenant.id))).all()
    return {r.name: str(r.id) for r in rows}


async def admin_client(
    method_client: MethodClient, make_user: MakeUser, tenant: Tenant, email: str
) -> AsyncClient:
    return await method_client(METHODS[0], await make_user(email, roles=["admin"], tenant=tenant))


async def test_each_tenant_has_its_own_default_roles(
    maker: async_sessionmaker[AsyncSession], tenant: Tenant, tenant_b: Tenant
) -> None:
    a, b = await roles_of(maker, tenant), await roles_of(maker, tenant_b)
    assert set(a) == set(b) == {"admin", "member"}
    assert not set(a.values()) & set(b.values())


async def test_roles_list_shows_only_own_tenant(
    maker: async_sessionmaker[AsyncSession],
    method_client: MethodClient,
    make_user: MakeUser,
    tenant: Tenant,
    tenant_b: Tenant,
) -> None:
    await add_role(maker, tenant_b, "auditor", ["users:read"])
    admin = await admin_client(method_client, make_user, tenant, "admin-a@example.com")
    items = (await admin.get(ROLES)).json()["items"]
    own = await roles_of(maker, tenant)
    assert {r["name"]: r["id"] for r in items} == own


async def test_cross_tenant_user_id_on_put_roles_is_404(
    method_client: MethodClient, make_user: MakeUser, tenant: Tenant, tenant_b: Tenant
) -> None:
    victim = await make_user("victim-b@example.com", tenant=tenant_b)
    admin = await admin_client(method_client, make_user, tenant, "admin-a@example.com")
    r = await admin.put(f"{USERS}/{victim.id}/roles", json={"roles": ["admin"]})
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    b_admin = await admin_client(method_client, make_user, tenant_b, "admin-b@example.com")
    listed = (await b_admin.get(USERS, params={"limit": 100})).json()["items"]
    assert next(u for u in listed if u["email"] == "victim-b@example.com")["roles"] == ["member"]


async def test_role_of_another_tenant_cannot_be_assigned(
    maker: async_sessionmaker[AsyncSession],
    method_client: MethodClient,
    make_user: MakeUser,
    tenant: Tenant,
    tenant_b: Tenant,
) -> None:
    await add_role(maker, tenant_b, "auditor", ["users:read"])
    target = await make_user("target-a@example.com", tenant=tenant)
    admin = await admin_client(method_client, make_user, tenant, "admin-a@example.com")
    put = await admin.put(f"{USERS}/{target.id}/roles", json={"roles": ["auditor"]})
    assert put.status_code == 422
    assert put.json()["code"] == "unknown_role"
    post = await admin.post(
        USERS,
        json={"email": "new@example.com", "password": "long-enough-pw", "roles": ["auditor"]},
    )
    assert post.status_code == 422
    assert post.json()["code"] == "unknown_role"


async def test_service_never_links_a_role_of_another_tenant(
    maker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
    tenant: Tenant,
    tenant_b: Tenant,
) -> None:
    """Even handed a foreign Role object, assignment only links this tenant's roles."""
    user = await make_user("a@example.com", roles=[], tenant=tenant)
    foreign = await add_role(maker, tenant_b, "auditor", ["users:read"])
    async with maker() as session:
        rbac = RbacService(session, tenant.id)
        await rbac.assign(user.id, [foreign])
        await session.commit()
        assert await rbac.role_names([user.id]) == {}
        assert await rbac.permissions_for(user.id) == set()
        links = (
            await session.execute(select(user_roles).where(user_roles.c.user_id == user.id))
        ).all()
        assert links == []


async def test_a_stray_cross_tenant_role_link_grants_nothing(
    maker: async_sessionmaker[AsyncSession],
    method_client: MethodClient,
    make_user: MakeUser,
    tenant: Tenant,
    tenant_b: Tenant,
) -> None:
    """Permissions are read through the user's own tenant's roles only."""
    user = await make_user("a@example.com", roles=[], tenant=tenant)
    b_admin_role = (await roles_of(maker, tenant_b))["admin"]
    async with maker() as session:
        await session.execute(
            insert(user_roles).values(user_id=user.id, role_id=UUID(b_admin_role))
        )
        await session.commit()
    c = await method_client(METHODS[0], user)
    assert (await c.get(USERS)).status_code == 403
    assert (await c.get(f"{USERS}/me")).json()["roles"] == []
