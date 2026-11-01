{%- set rbac = cookiecutter.use_rbac == "y" -%}
{%- set api_key = cookiecutter.use_auth_api_key == "y" -%}
{%- set social = cookiecutter.use_social_login == "y" -%}
"""Tenant management: superuser-only, atomic creation, deactivation instead of deletion."""
from uuid import uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import {% if rbac %}Role, {% endif %}Tenant, User
from tests.conftest import {% if api_key %}MakeApiKey, {% endif %}MakeUser
from tests.tenancy.conftest import HUMAN_METHODS, MethodClient

TENANTS = "/api/v1/tenants"
NEW = {
    "name": "Acme Corp",
    "slug": "acme",
    "admin_email": "boss@acme.example",
    "admin_password": "acme-admin-password",
}
TENANT_FIELDS = {"id", "name", "slug", "is_active", "created_at"{% if social %}, "allow_social_signup"{% endif %}}


@pytest_asyncio.fixture
async def root(
    method_client: MethodClient, make_user: MakeUser, system_tenant: Tenant
) -> AsyncClient:
    """A superuser (platform operator) of the system tenant."""
    user = await make_user("root@example.com", superuser=True, tenant=system_tenant)
    return await method_client(HUMAN_METHODS[0], user)


async def counts(maker: async_sessionmaker[AsyncSession]) -> tuple[int, ...]:
    async with maker() as session:
        models: list[type] = [Tenant, User{% if rbac %}, Role{% endif %}]
        return tuple(
            [await session.scalar(select(func.count()).select_from(m)) or 0 for m in models]
        )


async def test_tenant_crud_superuser_only(
    anon_client: AsyncClient,
    client: AsyncClient,
{%- if rbac %}
    method_client: MethodClient,
    make_user: MakeUser,
{%- endif %}
    tenant: Tenant,
) -> None:
    requests = [
        ("POST", TENANTS, NEW),
        ("GET", TENANTS, None),
        ("GET", f"{TENANTS}/{tenant.id}", None),
        ("PATCH", f"{TENANTS}/{tenant.id}", {"name": "mine now"}),
    ]
    denied = [client]
{%- if rbac %}
    # A tenant admin holds every permission, yet tenant management is not one of them.
    tenant_admin = await make_user("a@a.example", roles=["admin"])
    denied.append(await method_client(HUMAN_METHODS[0], tenant_admin))
{%- endif %}
    for method, url, body in requests:
        assert (await anon_client.request(method, url, json=body)).status_code == 401
        for c in denied:
            r = await c.request(method, url, json=body)
            assert r.status_code == 403, (method, url)
            assert r.json()["code"] == "forbidden"


async def test_create_tenant_creates_tenant{% if rbac %}_default_roles{% endif %}_and_admin(
    root: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    r = await root.post(TENANTS, json=NEW)
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == TENANT_FIELDS
    assert (body["name"], body["slug"], body["is_active"]) == ("Acme Corp", "acme", True)
    async with maker() as session:
        admin = (
            await session.scalars(select(User).where(User.email == "boss@acme.example"))
        ).one()
        assert str(admin.tenant_id) == body["id"]
        assert not admin.is_superuser
{%- if rbac %}
        roles = (await session.scalars(select(Role).where(Role.tenant_id == admin.tenant_id))).all()
        assert sorted(r.name for r in roles) == ["admin", "member"]
{%- endif %}


async def test_new_tenant_admin_can_log_in_and_sees_an_empty_tenant(
    root: AsyncClient,
    client: AsyncClient,
    method_client: MethodClient,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await client.post("/api/v1/items", json={"name": "tenant-a item"})
    created = (await root.post(TENANTS, json=NEW)).json()
    async with maker() as session:
        admin = (
            await session.scalars(select(User).where(User.email == "boss@acme.example"))
        ).one()
    c = await method_client(HUMAN_METHODS[0], admin, NEW["admin_password"])
    me = (await c.get("/api/v1/users/me")).json()
    assert me["tenant_id"] == created["id"]
{%- if rbac %}
    assert me["roles"] == ["admin"]
    assert [u["email"] for u in (await c.get("/api/v1/users")).json()["items"]] == [
        "boss@acme.example"
    ]
{%- endif %}
    assert (await c.get("/api/v1/items")).json()["total"] == 0
    assert (await c.get("/api/v2/items")).json()["data"] == []


async def test_duplicate_email_leaves_nothing_behind(
    root: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user("taken@example.com")
    before = await counts(maker)
    r = await root.post(TENANTS, json={**NEW, "admin_email": "Taken@example.com"})
    assert r.status_code == 409
    assert r.json()["code"] == "email_taken"
    assert await counts(maker) == before
    assert (await root.post(TENANTS, json=NEW)).status_code == 201  # the slug is still free


async def test_duplicate_slug_leaves_nothing_behind(
    root: AsyncClient, tenant: Tenant, maker: async_sessionmaker[AsyncSession]
) -> None:
    before = await counts(maker)
    r = await root.post(TENANTS, json={**NEW, "slug": tenant.slug})
    assert r.status_code == 409
    assert r.json()["code"] == "slug_taken"
    assert await counts(maker) == before


async def test_invalid_input_leaves_nothing_behind(
    root: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    before = await counts(maker)
    for bad in ("A", "Upper", "-lead", "trail-", "under_score", "x" * 41):
        assert (await root.post(TENANTS, json={**NEW, "slug": bad})).status_code == 422, bad
    short = await root.post(TENANTS, json={**NEW, "admin_password": "short"})
    assert short.status_code == 422
    assert await counts(maker) == before


async def test_list_get_and_patch_tenants(root: AsyncClient, tenant: Tenant) -> None:
    listed = (await root.get(TENANTS, params={"limit": 100})).json()
    assert {"system", tenant.slug} <= {t["slug"] for t in listed["items"]}
    assert listed["total"] == len(listed["items"])
    assert all(set(t) == TENANT_FIELDS for t in listed["items"])
    got = await root.get(f"{TENANTS}/{tenant.id}")
    assert got.json()["slug"] == tenant.slug
    assert (await root.get(f"{TENANTS}/{uuid4()}")).status_code == 404
    patched = await root.patch(
        f"{TENANTS}/{tenant.id}",
        json={"name": "Renamed", "slug": "ignored"{% if social %}, "allow_social_signup": True{% endif %}},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["slug"] == tenant.slug  # the slug never changes
{%- if social %}
    assert patched.json()["allow_social_signup"] is True
{%- endif %}
    assert (await root.patch(f"{TENANTS}/{tenant.id}", json={"name": None})).status_code == 422


async def test_system_tenant_cannot_be_deactivated(
    root: AsyncClient, system_tenant: Tenant
) -> None:
    r = await root.patch(f"{TENANTS}/{system_tenant.id}", json={"is_active": False})
    assert r.status_code == 409
    assert r.json()["code"] == "system_tenant"
    assert (await root.get("/api/v1/users/me")).status_code == 200


async def test_no_delete_endpoint(root: AsyncClient, tenant: Tenant) -> None:
    assert (await root.delete(f"{TENANTS}/{tenant.id}")).status_code == 405
{%- if api_key %}


async def test_api_key_cannot_create_tenants(
    anon_client: AsyncClient,
    make_user: MakeUser,
    make_api_key: MakeApiKey,
    system_tenant: Tenant,
) -> None:
    root = await make_user("root@example.com", superuser=True, tenant=system_tenant)
    r = await anon_client.post(TENANTS, json=NEW, headers={"X-API-Key": await make_api_key(root)})
    assert r.status_code == 403
    assert r.json()["code"] == "api_key_not_allowed"
{%- endif %}
