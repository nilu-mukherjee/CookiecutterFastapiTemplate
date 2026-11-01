from uuid import uuid4

from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Permission, Role, User
from app.services.rbac import RbacService
from tests.conftest import LoginClient, MakeUser

V1_ITEMS = "/api/v1/items"
V2_ITEMS = "/api/v2/items"
USERS = "/api/v1/users"
ROLES = "/api/v1/roles"


async def set_roles(
    maker: async_sessionmaker[AsyncSession], user: User, names: list[str]
) -> None:
    async with maker() as session:
        await RbacService(session).set_user_roles(user.id, names)


async def make_role(
    maker: async_sessionmaker[AsyncSession], name: str, permissions: list[str]
) -> None:
    """A custom role holding exactly the named permissions."""
    async with maker() as session:
        found = (
            await session.scalars(select(Permission).where(Permission.name.in_(permissions)))
        ).all()
        session.add(Role(name=name, permissions=list(found)))
        await session.commit()


def assert_denied(r: Response, permission: str) -> None:
    assert r.status_code == 403
    body = r.json()
    assert set(body) == {"code", "detail", "request_id"}
    assert body["code"] == "permission_denied"
    assert permission in body["detail"]


# --- the two default roles -----------------------------------------------------------


async def test_member_can_read_and_write_items_not_manage_users(client: AsyncClient) -> None:
    for base in (V1_ITEMS, V2_ITEMS):
        created = await client.post(base, json={"name": "mine"})
        assert created.status_code == 201
        item_id = created.json()["id"]
        assert (await client.get(base)).status_code == 200
        assert (await client.get(f"{base}/{item_id}")).status_code == 200
        assert (await client.patch(f"{base}/{item_id}", json={"name": "b"})).status_code == 200
        assert (await client.delete(f"{base}/{item_id}")).status_code == 204

    assert_denied(await client.get(USERS), "users:read")
    new = {"email": "new@example.com", "password": "brand-new-password"}
    assert_denied(await client.post(USERS, json=new), "users:write")
    assert_denied(await client.get(ROLES), "users:read")
    me = await client.get(f"{USERS}/me")
    assert me.status_code == 200
    assert me.json()["roles"] == ["member"]


async def test_admin_can_manage_users(make_admin: MakeUser, login_client: LoginClient) -> None:
    admin = await login_client(await make_admin())
    assert (await admin.get(USERS)).status_code == 200
    assert (await admin.get(ROLES)).status_code == 200
    new = {"email": "new@example.com", "password": "brand-new-password"}
    created = await admin.post(USERS, json=new)
    assert created.status_code == 201
    assert created.json()["roles"] == ["member"]
    assert (await admin.get(V1_ITEMS)).status_code == 200
    assert (await admin.post(V2_ITEMS, json={"name": "x"})).status_code == 201


async def test_missing_permission_403_with_code(
    make_user: MakeUser, login_client: LoginClient
) -> None:
    nobody = await login_client(await make_user("nobody@example.com", roles=[]))
    for base in (V1_ITEMS, V2_ITEMS):
        r = await nobody.get(base, headers={"X-Request-ID": "rid-42"})
        assert_denied(r, "items:read")
        assert r.json()["request_id"] == "rid-42"
        assert_denied(await nobody.post(base, json={"name": "x"}), "items:write")
        assert_denied(await nobody.get(f"{base}/{uuid4()}"), "items:read")
        assert_denied(await nobody.patch(f"{base}/{uuid4()}", json={"name": "x"}), "items:write")
        assert_denied(await nobody.delete(f"{base}/{uuid4()}"), "items:write")
    assert (await nobody.get(f"{USERS}/me")).status_code == 200


async def test_custom_role_with_only_read_permission(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser, login_client: LoginClient
) -> None:
    await make_role(maker, "reader", ["items:read"])
    reader = await login_client(await make_user("reader@example.com", roles=["reader"]))
    assert (await reader.get(V1_ITEMS)).status_code == 200
    assert (await reader.get(V2_ITEMS)).status_code == 200
    assert_denied(await reader.post(V1_ITEMS, json={"name": "x"}), "items:write")
    assert_denied(await reader.post(V2_ITEMS, json={"name": "x"}), "items:write")


async def test_unauthenticated_is_401_not_403(anon_client: AsyncClient) -> None:
    assert (await anon_client.get(V1_ITEMS)).status_code == 401
    assert (await anon_client.post(V2_ITEMS, json={"name": "x"})).status_code == 401
    assert (await anon_client.get(USERS)).status_code == 401
    assert (await anon_client.get(ROLES)).status_code == 401


async def test_superuser_bypass(make_user: MakeUser, login_client: LoginClient) -> None:
    root = await login_client(await make_user("root@example.com", superuser=True, roles=[]))
    assert (await root.get(USERS)).status_code == 200
    assert (await root.get(ROLES)).status_code == 200
    assert (await root.post(V1_ITEMS, json={"name": "x"})).status_code == 201
    assert (await root.get(V2_ITEMS)).status_code == 200


async def test_role_change_takes_effect_on_next_request(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser, login_client: LoginClient
) -> None:
    user = await make_user("flip@example.com")
    c = await login_client(user)
    assert (await c.get(V1_ITEMS)).status_code == 200
    await set_roles(maker, user, [])
    assert_denied(await c.get(V1_ITEMS), "items:read")
    await set_roles(maker, user, ["admin"])
    assert (await c.get(USERS)).status_code == 200


async def test_permissions_are_not_cached_across_requests(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser, login_client: LoginClient
) -> None:
    user = await make_user("cache@example.com")
    c = await login_client(user)
    for _ in range(2):
        assert (await c.get(V1_ITEMS)).status_code == 200
    await set_roles(maker, user, [])
    assert (await c.get(V1_ITEMS)).status_code == 403
    assert (await c.get(V1_ITEMS)).status_code == 403


# --- creating users --------------------------------------------------------------------


async def test_create_user_gets_member_by_default_and_optional_roles(
    make_admin: MakeUser, login_client: LoginClient
) -> None:
    admin = await login_client(await make_admin())
    pw = "brand-new-password"
    plain = await admin.post(USERS, json={"email": "a@example.com", "password": pw})
    assert plain.json()["roles"] == ["member"]
    both = await admin.post(
        USERS, json={"email": "b@example.com", "password": pw, "roles": ["member", "admin"]}
    )
    assert both.status_code == 201
    assert both.json()["roles"] == ["admin", "member"]
    none = await admin.post(USERS, json={"email": "c@example.com", "password": pw, "roles": []})
    assert none.status_code == 201
    assert none.json()["roles"] == []


async def test_create_user_with_unknown_role_is_422_and_creates_nothing(
    make_admin: MakeUser, login_client: LoginClient
) -> None:
    admin = await login_client(await make_admin())
    r = await admin.post(
        USERS,
        json={"email": "x@example.com", "password": "brand-new-password", "roles": ["wizard"]},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "unknown_role"
    assert "wizard" in r.json()["detail"]
    emails = [u["email"] for u in (await admin.get(USERS)).json()["items"]]
    assert "x@example.com" not in emails


async def test_non_superuser_cannot_create_a_superuser(
    make_admin: MakeUser, make_user: MakeUser, login_client: LoginClient
) -> None:
    admin = await login_client(await make_admin())
    body = {"email": "su@example.com", "password": "brand-new-password", "is_superuser": True}
    r = await admin.post(USERS, json=body)
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    root = await login_client(await make_user("root@example.com", superuser=True))
    assert (await root.post(USERS, json=body)).status_code == 201


async def test_list_users_includes_roles(make_admin: MakeUser, login_client: LoginClient) -> None:
    admin = await login_client(await make_admin("admin@example.com"))
    items = (await admin.get(USERS)).json()["items"]
    assert {u["email"]: u["roles"] for u in items} == {"admin@example.com": ["admin"]}


# --- role management -------------------------------------------------------------------


async def test_list_roles_with_permissions(make_admin: MakeUser, login_client: LoginClient) -> None:
    admin = await login_client(await make_admin())
    r = await admin.get(ROLES)
    assert r.status_code == 200
    roles = {x["name"]: x["permissions"] for x in r.json()["items"]}
    assert roles == {
        "admin": ["items:read", "items:write", "users:read", "users:write"],
        "member": ["items:read", "items:write"],
    }


async def test_put_roles_replaces_the_role_set(
    make_admin: MakeUser, make_user: MakeUser, login_client: LoginClient
) -> None:
    admin = await login_client(await make_admin())
    target = await make_user("target@example.com")
    r = await admin.put(f"{USERS}/{target.id}/roles", json={"roles": ["admin"]})
    assert r.status_code == 200
    assert r.json()["roles"] == ["admin"]
    assert r.json()["id"] == str(target.id)
    r = await admin.put(f"{USERS}/{target.id}/roles", json={"roles": ["member", "admin"]})
    assert r.json()["roles"] == ["admin", "member"]
    r = await admin.put(f"{USERS}/{target.id}/roles", json={"roles": []})
    assert r.status_code == 200
    assert r.json()["roles"] == []
    # The new role set is what the target's next request is checked against.
    t = await login_client(target)
    assert_denied(await t.get(V1_ITEMS), "items:read")


async def test_put_roles_unknown_role_is_422_and_changes_nothing(
    make_admin: MakeUser, make_user: MakeUser, login_client: LoginClient
) -> None:
    admin = await login_client(await make_admin())
    target = await make_user("target@example.com")
    r = await admin.put(f"{USERS}/{target.id}/roles", json={"roles": ["admin", "wizard"]})
    assert r.status_code == 422
    assert r.json()["code"] == "unknown_role"
    items = (await admin.get(USERS)).json()["items"]
    assert {u["email"]: u["roles"] for u in items}["target@example.com"] == ["member"]


async def test_put_roles_unknown_user_is_404(
    make_admin: MakeUser, login_client: LoginClient
) -> None:
    admin = await login_client(await make_admin())
    r = await admin.put(f"{USERS}/{uuid4()}/roles", json={"roles": ["member"]})
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


async def test_non_superuser_cannot_modify_own_roles(
    make_admin: MakeUser, login_client: LoginClient
) -> None:
    me = await make_admin()
    admin = await login_client(me)
    r = await admin.put(f"{USERS}/{me.id}/roles", json={"roles": ["member"]})
    assert r.status_code == 403
    assert r.json()["code"] == "cannot_modify_own_roles"
    # Still an admin afterwards.
    assert (await admin.get(USERS)).status_code == 200


async def test_superuser_can_modify_own_roles(
    make_user: MakeUser, login_client: LoginClient
) -> None:
    me = await make_user("root@example.com", superuser=True)
    root = await login_client(me)
    r = await root.put(f"{USERS}/{me.id}/roles", json={"roles": ["admin"]})
    assert r.status_code == 200
    assert r.json()["roles"] == ["admin"]


async def test_put_roles_requires_users_write(
    maker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
    login_client: LoginClient,
) -> None:
    await make_role(maker, "auditor", ["users:read"])
    target = await make_user("target@example.com")
    member = await login_client(await make_user("m@example.com"))
    auditor = await login_client(await make_user("a@example.com", roles=["auditor"]))
    for c in (member, auditor):
        assert_denied(await c.put(f"{USERS}/{target.id}/roles", json={"roles": []}), "users:write")
    # users:read alone lists users and roles.
    assert (await auditor.get(USERS)).status_code == 200
    assert (await auditor.get(ROLES)).status_code == 200


async def test_put_roles_requires_authentication(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    target = await make_user("target@example.com")
    r = await anon_client.put(f"{USERS}/{target.id}/roles", json={"roles": []})
    assert r.status_code == 401


async def test_openapi_documents_the_role_endpoints(anon_client: AsyncClient) -> None:
    paths = (await anon_client.get("/api/v1/openapi.json")).json()["paths"]
    assert "get" in paths["/roles"]
    assert "put" in paths["/users/{user_id}/roles"]
    assert {"get", "post"} <= set(paths["/items"])
    assert {"get", "post"} <= set(paths["/users"])
