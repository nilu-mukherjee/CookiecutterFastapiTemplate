{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
import base64
import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import {% if bearer %}Callable, {% endif %}Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from httpx import AsyncClient, Response
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.main import create_app
from app.models import User
from tests.conftest import CSRF_HEADER, PASSWORD, MakeCookieClient, MakeUser

ITEMS = "/api/v1/items"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
STRONG = "x" * 40
{%- if bearer %}
Headers = Callable[[User], dict[str, str]]
{%- endif %}


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- helpers ------------------------------------------------------------------


def _parse_set_cookie(raw: str) -> tuple[str, str, dict[str, str]]:
    first, *rest = [part.strip() for part in raw.split(";")]
    name, _, value = first.partition("=")
    attrs: dict[str, str] = {}
    for attr in rest:
        key, _, val = attr.partition("=")
        attrs[key.lower()] = val
    return name, value.strip('"'), attrs


def _set_cookies(r: Response) -> dict[str, tuple[str, dict[str, str]]]:
    out = {}
    for raw in r.headers.get_list("set-cookie"):
        name, value, attrs = _parse_set_cookie(raw)
        out[name] = (value, attrs)
    return out


async def _cookie_login(c: AsyncClient, email: str) -> dict[str, str]:
    """Log in with cookie delivery; return the cookie values and keep the jar empty."""
    r = await c.post(LOGIN, json={"email": email, "password": PASSWORD, "delivery": "cookie"})
    assert r.status_code == 200, r.text
    c.cookies.clear()
    return {name: value for name, (value, _) in _set_cookies(r).items()}


async def _raw(
    c: AsyncClient,
    method: str,
    url: str,
    cookies: dict[str, str],
    csrf: str | None = None,
    **kwargs: Any,
) -> Response:
    """Send exactly these cookies (and CSRF header), bypassing the client's jar."""
    c.cookies.clear()
    headers = dict(kwargs.pop("headers", {}))
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    if csrf is not None:
        headers[CSRF_HEADER] = csrf
    return await c.request(method, url, headers=headers, **kwargs)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _csrf_for(user_id: uuid.UUID, key: str | None = None, nonce: str | None = None) -> str:
    """Independent implementation of the documented CSRF token format."""
    nonce = nonce or secrets.token_urlsafe(16)
    secret = (key or get_settings().secret_key).encode()
    mac = hmac.new(secret, f"csrf:{user_id}:{nonce}".encode(), hashlib.sha256).digest()
    return f"{nonce}.{_b64(mac)}"


def _access_claims(user: User, **overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user.id),
        "jti": uuid.uuid4().hex,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


def _assert_csrf_failed(r: Response) -> None:
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["code"] == "csrf_failed"
    assert "request_id" in body


async def _assert_cookie_rejected(c: AsyncClient, access: str) -> None:
    r = await _raw(c, "GET", ITEMS, {"access_token": access})
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "invalid_credentials"
    assert "www-authenticate" not in r.headers


# --- login --------------------------------------------------------------------


async def test_login_sets_httponly_cookie_and_csrf_cookie(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("jar@example.com")
    r = await anon_client.post(
        LOGIN, json={"email": "jar@example.com", "password": PASSWORD, "delivery": "cookie"}
    )
    assert r.status_code == 200, r.text
    settings = get_settings()
    access_age = str(settings.access_token_expire_minutes * 60)
    refresh_age = str(settings.refresh_token_expire_days * 86400)
    cookies = _set_cookies(r)
    assert set(cookies) == {"access_token", "refresh_token", "csrf_token"}

    access, a = cookies["access_token"]
    assert "httponly" in a and a["path"] == "/" and a["max-age"] == access_age
    refresh, rf = cookies["refresh_token"]
    assert "httponly" in rf and rf["path"] == "/api/v1/auth" and rf["max-age"] == refresh_age
    csrf, cs = cookies["csrf_token"]
    assert "httponly" not in cs and cs["path"] == "/" and cs["max-age"] == refresh_age
    for _, attrs in cookies.values():
        assert attrs["samesite"] == "lax"
        assert "domain" not in attrs
        assert "secure" not in attrs  # dev default; non-dev requires COOKIE_SECURE

    # The body carries no token, only what JS needs.
    body = r.json()
    assert set(body) == {"token_type", "expires_in", "csrf_token"}
    assert body["token_type"] == "cookie"
    assert body["expires_in"] == int(access_age)
    assert body["csrf_token"] == csrf
    assert access not in r.text and refresh not in r.text
    assert jwt.decode(access, settings.secret_key, algorithms=["HS256"])["type"] == "access"


async def test_cookie_attributes_follow_settings(
    anon_client: AsyncClient,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    await make_user("attrs@example.com")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("COOKIE_SAMESITE", "strict")
    monkeypatch.setenv("COOKIE_DOMAIN", "example.test")
    get_settings.cache_clear()
    r = await anon_client.post(
        LOGIN, json={"email": "attrs@example.com", "password": PASSWORD, "delivery": "cookie"}
    )
    assert r.status_code == 200, r.text
    cookies = _set_cookies(r)
    assert set(cookies) == {"access_token", "refresh_token", "csrf_token"}
    for _, attrs in cookies.values():
        assert "secure" in attrs
        assert attrs["samesite"] == "strict"
        assert attrs["domain"] == "example.test"


async def test_login_via_get_not_allowed(anon_client: AsyncClient) -> None:
    r = await anon_client.get(LOGIN, params={"email": "a@example.com", "password": PASSWORD})
    assert r.status_code == 405


async def test_unknown_delivery_rejected(anon_client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("deliv@example.com")
    creds = {"email": "deliv@example.com", "password": PASSWORD}
    r = await anon_client.post(LOGIN, json={**creds, "delivery": "carrier-pigeon"})
    assert r.status_code == 422
{%- if bearer %}
    # Bearer is enabled, so it is the default delivery: tokens in the body, no cookies.
    default = await anon_client.post(LOGIN, json=creds)
    assert default.status_code == 200
    assert default.json()["token_type"] == "bearer"
    assert "set-cookie" not in default.headers
{%- else %}
    # Bearer is not enabled: asking for it is a validation error, cookie is the default.
    assert (await anon_client.post(LOGIN, json={**creds, "delivery": "bearer"})).status_code == 422
    default = await anon_client.post(LOGIN, json=creds)
    assert default.status_code == 200
    assert default.json()["token_type"] == "cookie"
    assert "access_token" not in default.json()
{%- endif %}


# --- cookie authenticator + CSRF ------------------------------------------------


async def test_cookie_get_items_ok_without_csrf(
    make_user: MakeUser, make_cookie_client: MakeCookieClient
) -> None:
    await make_user("reader@example.com")
    c = await make_cookie_client("reader@example.com", csrf_header=False)
    assert (await c.get(ITEMS)).status_code == 200
    me = await c.get("/api/v1/users/me")
    assert me.status_code == 200
    assert me.json()["email"] == "reader@example.com"


async def test_cookie_post_without_csrf_header_403(
    make_user: MakeUser, make_cookie_client: MakeCookieClient
) -> None:
    await make_user("nocsrf@example.com")
    c = await make_cookie_client("nocsrf@example.com", csrf_header=False)
    _assert_csrf_failed(await c.post(ITEMS, json={"name": "x"}))


async def test_cookie_post_with_matching_csrf_ok(
    make_user: MakeUser, make_cookie_client: MakeCookieClient
) -> None:
    await make_user("matching@example.com")
    c = await make_cookie_client("matching@example.com")
    created = await c.post(ITEMS, json={"name": "mine"})
    assert created.status_code == 201, created.text
{%- if not bearer %}


async def test_client_fixture_uses_cookie_login(client: AsyncClient) -> None:
    # Cookie-only build: ``client`` is the real cookie login plus the CSRF header.
    assert "authorization" not in client.headers
    assert client.headers[CSRF_HEADER] == client.cookies["csrf_token"]
    assert (await client.post(ITEMS, json={"name": "via-fixture"})).status_code == 201
{%- endif %}


async def test_cookie_unsafe_methods_with_matching_csrf_ok(
    make_user: MakeUser, make_cookie_client: MakeCookieClient
) -> None:
    await make_user("writer@example.com")
    c = await make_cookie_client("writer@example.com")
    created = await c.post(ITEMS, json={"name": "a"})
    assert created.status_code == 201, created.text
    item = created.json()["id"]
    assert (await c.patch(f"{ITEMS}/{item}", json={"name": "b"})).status_code == 200
    assert (await c.delete(f"{ITEMS}/{item}")).status_code == 204


async def test_mismatched_csrf_403(
    anon_client: AsyncClient, make_user: MakeUser, make_cookie_client: MakeCookieClient
) -> None:
    user = await make_user("mismatch@example.com")
    jar = await _cookie_login(anon_client, "mismatch@example.com")
    access, csrf = jar["access_token"], jar["csrf_token"]
    payload = {"json": {"name": "x"}}

    # Header is a validly signed token for this user, but not the cookie's value.
    other = _csrf_for(user.id)
    r = await _raw(anon_client, "POST", ITEMS, {"access_token": access, "csrf_token": csrf},
                   other, **payload)
    _assert_csrf_failed(r)
    # Header present, cookie missing.
    _assert_csrf_failed(
        await _raw(anon_client, "POST", ITEMS, {"access_token": access}, csrf, **payload)
    )
    # Cookie present, header garbage / empty.
    for bad in ("garbage", ""):
        r = await _raw(anon_client, "POST", ITEMS, {"access_token": access, "csrf_token": csrf},
                       bad, **payload)
        _assert_csrf_failed(r)
    # Sanity: the correct pair works.
    ok = await _raw(anon_client, "POST", ITEMS, {"access_token": access, "csrf_token": csrf},
                    csrf, **payload)
    assert ok.status_code == 201, ok.text

    # A client whose jar lost the CSRF cookie but still sends the header.
    c = await make_cookie_client("mismatch@example.com")
    c.cookies.delete("csrf_token")
    _assert_csrf_failed(await c.post(ITEMS, json={"name": "y"}))


async def test_forged_csrf_mac_403(anon_client: AsyncClient, make_user: MakeUser) -> None:
    """Header == cookie is not enough: the token must carry the server's MAC."""
    user = await make_user("forge@example.com")
    access = (await _cookie_login(anon_client, "forge@example.com"))["access_token"]
    forgeries = [
        _csrf_for(user.id, key="attacker-chosen-key-" + "k" * 32),
        f"{secrets.token_urlsafe(16)}.{_b64(secrets.token_bytes(32))}",
        _csrf_for(uuid.uuid4()),  # signed by the server key, but for another user id
        "no-dot-at-all",
        ".",
        "a.b.c",
        _csrf_for(user.id) + "x",
    ]
    for forged in forgeries:
        r = await _raw(anon_client, "POST", ITEMS, {"access_token": access, "csrf_token": forged},
                       forged, json={"name": "x"})
        _assert_csrf_failed(r)


async def test_csrf_token_for_user_a_rejected_for_user_b(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("alice@example.com")
    await make_user("bob@example.com")
    alice = await _cookie_login(anon_client, "alice@example.com")
    bob = await _cookie_login(anon_client, "bob@example.com")
    r = await _raw(
        anon_client, "POST", ITEMS,
        {"access_token": bob["access_token"], "csrf_token": alice["csrf_token"]},
        alice["csrf_token"], json={"name": "x"},
    )
    _assert_csrf_failed(r)
    ok = await _raw(
        anon_client, "POST", ITEMS,
        {"access_token": bob["access_token"], "csrf_token": bob["csrf_token"]},
        bob["csrf_token"], json={"name": "x"},
    )
    assert ok.status_code == 201, ok.text
{%- if bearer %}


async def test_bearer_post_without_csrf_ok(
    anon_client: AsyncClient, make_user: MakeUser, auth_headers: Headers
) -> None:
    user = await make_user("bearer@example.com")
    r = await anon_client.post(ITEMS, json={"name": "x"}, headers=auth_headers(user))
    assert r.status_code == 201, r.text
    # A valid Bearer header decides even when cookies are also present (no CSRF).
    jar = await _cookie_login(anon_client, "bearer@example.com")
    r = await _raw(anon_client, "POST", ITEMS, jar, headers=auth_headers(user),
                   json={"name": "y"})
    assert r.status_code == 201, r.text


async def test_invalid_bearer_with_valid_cookie_401(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("both@example.com")
    jar = await _cookie_login(anon_client, "both@example.com")
    for method in ("GET", "POST"):
        r = await _raw(anon_client, method, ITEMS, jar, jar["csrf_token"],
                       headers={"Authorization": "Bearer not-a-jwt"}, json={"name": "x"})
        assert r.status_code == 401, r.text
        assert r.json()["code"] == "invalid_credentials"


async def test_body_refresh_token_uses_bearer_flow(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("bodyflow@example.com")
    creds = {"email": "bodyflow@example.com", "password": PASSWORD}
    pair = (await anon_client.post(LOGIN, json=creds)).json()
    r = await anon_client.post(REFRESH, json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 200, r.text
    assert r.json()["token_type"] == "bearer"
    assert "set-cookie" not in r.headers
{%- endif %}


async def test_bad_access_cookie_401(anon_client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user("badcookie@example.com")
    key = get_settings().secret_key
    past = datetime.now(UTC) - timedelta(minutes=10)
    expired = jwt.encode(
        _access_claims(user, iat=past, exp=past + timedelta(minutes=1)), key, algorithm="HS256"
    )
    await _assert_cookie_rejected(anon_client, expired)

    good = jwt.encode(_access_claims(user), key, algorithm="HS256")
    head, payload, sig = good.split(".")
    flipped = sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    await _assert_cookie_rejected(anon_client, f"{head}.{payload}.{flipped}")
    await _assert_cookie_rejected(
        anon_client, jwt.encode(_access_claims(user), "another-key-" + "z" * 32, algorithm="HS256")
    )

    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    body = _b64(json.dumps(
        {**_access_claims(user), "iat": exp - 300, "exp": exp}, separators=(",", ":")
    ).encode())
    for alg in ("none", "None"):
        header = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
        await _assert_cookie_rejected(anon_client, f"{header}.{body}.")
    await _assert_cookie_rejected(anon_client, "not-a-jwt")


async def test_deactivated_user_cookie_401(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user("deact@example.com")
    jar = await _cookie_login(anon_client, "deact@example.com")
    assert (await _raw(anon_client, "GET", ITEMS, jar)).status_code == 200
    async with maker() as session:
        await session.execute(update(User).where(User.id == user.id).values(is_active=False))
        await session.commit()
    await _assert_cookie_rejected(anon_client, jar["access_token"])


async def test_unauthenticated_challenge_header(anon_client: AsyncClient) -> None:
    r = await anon_client.get(ITEMS)
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
{%- if bearer %}
    assert r.headers["www-authenticate"] == "Bearer"
{%- else %}
    assert "www-authenticate" not in r.headers
{%- endif %}


async def test_openapi_advertises_cookie_scheme(anon_client: AsyncClient) -> None:
    for version in ("v1", "v2"):
        schema = (await anon_client.get(f"/api/{version}/openapi.json")).json()
        schemes = list(schema["components"]["securitySchemes"].values())
        assert {"type": "apiKey", "in": "cookie", "name": "access_token"} in schemes
{%- if bearer %}
        assert {"type": "http", "scheme": "bearer"} in schemes
{%- else %}
        assert not any(s.get("scheme") == "bearer" for s in schemes)
{%- endif %}


# --- refresh / logout (cookie flow) ----------------------------------------------


async def test_cookie_refresh_rotates_cookies_and_old_refresh_rejected(
    anon_client: AsyncClient, make_user: MakeUser, make_cookie_client: MakeCookieClient
) -> None:
    user = await make_user("rotate@example.com")
    c = await make_cookie_client("rotate@example.com")
    old_refresh = c.cookies["refresh_token"]
    old_access = c.cookies["access_token"]
    old_csrf = c.cookies["csrf_token"]

    r = await c.post(REFRESH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"token_type", "expires_in", "csrf_token"}
    cookies = _set_cookies(r)
    assert set(cookies) == {"access_token", "refresh_token", "csrf_token"}
    assert cookies["refresh_token"][0] != old_refresh
    assert cookies["access_token"][0] != old_access
    assert cookies["csrf_token"][0] != old_csrf
    assert cookies["csrf_token"][0] == body["csrf_token"]
    assert cookies["refresh_token"][1]["path"] == "/api/v1/auth"

    c.headers[CSRF_HEADER] = body["csrf_token"]
    assert (await c.post(ITEMS, json={"name": "after-refresh"})).status_code == 201

    # Replaying the rotated refresh cookie (with a valid CSRF pair) is rejected.
    csrf = _csrf_for(user.id)
    replay = await _raw(anon_client, "POST", REFRESH,
                        {"refresh_token": old_refresh, "csrf_token": csrf}, csrf)
    assert replay.status_code == 401, replay.text
    assert replay.json()["code"] == "invalid_credentials"


async def test_cookie_refresh_and_logout_without_csrf_403(
    make_user: MakeUser, make_cookie_client: MakeCookieClient
) -> None:
    await make_user("refcsrf@example.com")
    c = await make_cookie_client("refcsrf@example.com", csrf_header=False)
    _assert_csrf_failed(await c.post(REFRESH))
    _assert_csrf_failed(await c.post(LOGOUT))
    c.headers[CSRF_HEADER] = "forged.value"
    _assert_csrf_failed(await c.post(REFRESH))
    # Neither attempt consumed or revoked the refresh token.
    c.headers[CSRF_HEADER] = c.cookies["csrf_token"]
    assert (await c.post(REFRESH)).status_code == 200


async def test_cookie_refresh_without_cookie_401(anon_client: AsyncClient) -> None:
    r = await anon_client.post(REFRESH)
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


async def test_cookie_logout_clears_cookies_and_revokes(
    anon_client: AsyncClient, make_user: MakeUser, make_cookie_client: MakeCookieClient
) -> None:
    user = await make_user("logout@example.com")
    c = await make_cookie_client("logout@example.com")
    refresh = c.cookies["refresh_token"]

    r = await c.post(LOGOUT)
    assert r.status_code == 204, r.text
    cookies = _set_cookies(r)
    assert set(cookies) == {"access_token", "refresh_token", "csrf_token"}
    expected_paths = {"access_token": "/", "refresh_token": "/api/v1/auth", "csrf_token": "/"}
    for name, (value, attrs) in cookies.items():
        assert value == ""
        assert attrs["max-age"] == "0"
        assert attrs["path"] == expected_paths[name]
        assert "domain" not in attrs
    assert (await c.get(ITEMS)).status_code == 401

    csrf = _csrf_for(user.id)
    again = await _raw(anon_client, "POST", REFRESH, {"refresh_token": refresh, "csrf_token": csrf},
                       csrf)
    assert again.status_code == 401


async def test_cookie_logout_without_refresh_cookie_is_idempotent(
    anon_client: AsyncClient,
) -> None:
    r = await anon_client.post(LOGOUT)
    assert r.status_code == 204
    assert {"access_token", "refresh_token", "csrf_token"} <= set(_set_cookies(r))


# --- settings -----------------------------------------------------------------


def test_cookie_settings_refuse_insecure_outside_dev(fresh_settings: None) -> None:
    for env in ("staging", "prod"):
        with pytest.raises(ValidationError, match="COOKIE_SECURE"):
            Settings(environment=env, secret_key=STRONG, cookie_secure=False)
        Settings(environment=env, secret_key=STRONG, cookie_secure=True)
    Settings(environment="dev", secret_key=STRONG, cookie_secure=False)
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(environment="dev", secret_key=STRONG, cookie_samesite="none")
    Settings(environment="dev", secret_key=STRONG, cookie_samesite="none", cookie_secure=True)


def test_startup_refused_in_non_dev_without_cookie_secure(
    monkeypatch: pytest.MonkeyPatch, fresh_settings: None
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SECRET_KEY", STRONG)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        create_app()
