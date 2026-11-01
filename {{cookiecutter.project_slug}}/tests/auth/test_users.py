{%- set rbac = cookiecutter.use_rbac == "y" -%}
from collections.abc import Callable

from httpx import AsyncClient

from app.models import User
from tests.conftest import MakeUser

Headers = Callable[[User], dict[str, str]]


async def test_me_returns_current_user(
    anon_client: AsyncClient, make_user: MakeUser, auth_headers: Headers
) -> None:
    user = await make_user("me@example.com")
    r = await anon_client.get("/api/v1/users/me", headers=auth_headers(user))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(user.id)
    assert body["email"] == "me@example.com"
    assert body["is_superuser"] is False
    assert "hashed_password" not in body
    assert (await anon_client.get("/api/v1/users/me")).status_code == 401


async def test_only_superuser_can_create_users(
    anon_client: AsyncClient, client: AsyncClient, make_user: MakeUser, auth_headers: Headers
) -> None:
    new = {"email": "New@Example.com", "password": "brand-new-password"}
    assert (await anon_client.post("/api/v1/users", json=new)).status_code == 401
    forbidden = await client.post("/api/v1/users", json=new)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "{% if rbac %}permission_denied{% else %}forbidden{% endif %}"

    admin = auth_headers(await make_user("root@example.com", superuser=True))
    created = await anon_client.post("/api/v1/users", json=new, headers=admin)
    assert created.status_code == 201
    assert created.json()["email"] == "new@example.com"
    assert created.json()["is_superuser"] is False

    dup = await anon_client.post("/api/v1/users", json=new, headers=admin)
    assert dup.status_code == 409
    short = {"email": "s@example.com", "password": "123456789"}
    assert (await anon_client.post("/api/v1/users", json=short, headers=admin)).status_code == 422
    bad_email = {"email": "not-an-email", "password": "long-enough-pass"}
    r = await anon_client.post("/api/v1/users", json=bad_email, headers=admin)
    assert r.status_code == 422

    login = await anon_client.post(
        "/api/v1/auth/login", json={"email": "new@example.com", "password": new["password"]}
    )
    assert login.status_code == 200


async def test_no_open_registration_route(anon_client: AsyncClient) -> None:
    paths = (await anon_client.get("/api/v1/openapi.json")).json()["paths"]
    assert not any("register" in p or "signup" in p for p in paths)


async def test_only_superuser_can_list_users(
    anon_client: AsyncClient, client: AsyncClient, make_user: MakeUser, auth_headers: Headers
) -> None:
    assert (await client.get("/api/v1/users")).status_code == 403
    assert (await anon_client.get("/api/v1/users")).status_code == 401
    admin = auth_headers(await make_user("root@example.com", superuser=True))
    for i in range(3):
        await make_user(f"u{i}@example.com")
    r = await anon_client.get("/api/v1/users", params={"limit": 2, "offset": 1}, headers=admin)
    assert r.status_code == 200
    body = r.json()
    # member@example.com (client fixture) + root + 3 users
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2
