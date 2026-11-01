{%- set api_key = cookiecutter.use_auth_api_key == "y" -%}
{%- set basic = cookiecutter.use_auth_basic == "y" -%}
{%- set names = ["MakeUser"] -%}
{%- if api_key %}{% set _ = names.append("MakeApiKey") %}{% endif -%}
{%- if basic %}{% set _ = names.append("basic_header") %}{% endif -%}
"""The first authenticator that finds its credential decides; there is no fall-through."""
{% if api_key %}from collections.abc import Callable

{% endif %}from httpx import AsyncClient

{% if api_key %}from app.models import User
{% endif %}from tests.conftest import {{ names | sort(case_sensitive=true) | join(", ") }}

ME = "/api/v1/users/me"
{%- if api_key %}
Headers = Callable[[User], dict[str, str]]
{%- endif %}
INVALID_BEARER = {"Authorization": "Bearer not.a.token"}
{%- if api_key %}


async def test_invalid_bearer_plus_valid_api_key_is_401(
    anon_client: AsyncClient, make_user: MakeUser, make_api_key: MakeApiKey
) -> None:
    user = await make_user("both@example.com")
    key = await make_api_key(user)
    assert (await anon_client.get(ME, headers={"X-API-Key": key})).status_code == 200
    r = await anon_client.get(ME, headers={**INVALID_BEARER, "X-API-Key": key})
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"
    assert r.headers["www-authenticate"].startswith("Bearer")


async def test_valid_bearer_wins_over_an_invalid_api_key(
    anon_client: AsyncClient, make_user: MakeUser, auth_headers: Headers
) -> None:
    user = await make_user("bearer-first@example.com")
    headers = {**auth_headers(user), "X-API-Key": "00000000.garbage"}
    r = await anon_client.get(ME, headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "bearer-first@example.com"


async def test_api_key_in_the_authorization_header_is_not_accepted(
    anon_client: AsyncClient, make_user: MakeUser, make_api_key: MakeApiKey
) -> None:
    key = await make_api_key(await make_user("misplaced@example.com"))
    r = await anon_client.get(ME, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"
{%- endif %}
{%- if basic %}


async def test_basic_header_is_not_parsed_by_bearer(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("basic-only@example.com")
    ok = await anon_client.get(ME, headers=basic_header("basic-only@example.com"))
    assert ok.status_code == 200
    # A bad Basic credential is a Basic failure: no Bearer challenge, no Bearer wording.
    bad = await anon_client.get(ME, headers=basic_header("basic-only@example.com", "wrong"))
    assert bad.status_code == 401
    assert bad.json()["code"] == "invalid_credentials"
    assert bad.json()["detail"] == "Invalid email or password"
    assert "www-authenticate" not in bad.headers


async def test_bearer_header_is_not_parsed_by_basic(anon_client: AsyncClient) -> None:
    r = await anon_client.get(ME, headers=INVALID_BEARER)
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired token"
    assert r.headers["www-authenticate"].startswith("Bearer")
{%- endif %}
{%- if api_key and basic %}


async def test_api_key_decides_before_basic(
    anon_client: AsyncClient, make_user: MakeUser, make_api_key: MakeApiKey
) -> None:
    user = await make_user("order@example.com")
    key = await make_api_key(user)
    garbage_basic = {"Authorization": "Basic !!!"}
    good_basic = basic_header("order@example.com")
    # A valid key is not undone by a broken Basic header (the key is found first) ...
    assert (
        await anon_client.get(ME, headers={"X-API-Key": key, **garbage_basic})
    ).status_code == 200
    # ... and a valid Basic header is not rescued by it: a bad key is final.
    bad = await anon_client.get(ME, headers={"X-API-Key": "00000000.garbage", **good_basic})
    assert bad.status_code == 401
    assert bad.json()["code"] == "invalid_credentials"
{%- endif %}
