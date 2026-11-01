{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.models import Session, User
from tests.conftest import CSRF_HEADER, PASSWORD, MakeSessionClient, MakeUser

ITEMS = "/api/v1/items"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/users/me"
STRONG = "x" * 40
ABSOLUTE_SECONDS = 12 * 3600  # default SESSION_ABSOLUTE_LIFETIME_HOURS


# --- helpers ------------------------------------------------------------------


class Clock:
    """Controllable replacement for ``app.core.time.utcnow`` (tests never sleep)."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr("app.core.time.utcnow", fake)
    return fake


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


async def _rows(maker: async_sessionmaker[AsyncSession]) -> list[Session]:
    async with maker() as db:
        return list((await db.scalars(select(Session).order_by(Session.created_at))).all())


async def _login(c: AsyncClient, email: str) -> Response:
    r = await c.post(
        LOGIN, json={"email": email, "password": PASSWORD, "delivery": "session"}
    )
    assert r.status_code == 200, r.text
    return r


async def _fresh_jar_login(c: AsyncClient, email: str) -> dict[str, str]:
    """Log in, return the cookie values, and leave the client's jar empty."""
    r = await _login(c, email)
    c.cookies.clear()
    return {name: value for name, (value, _) in _set_cookies(r).items()} | {
        "csrf": r.json()["csrf_token"]
    }


async def _raw(
    c: AsyncClient, method: str, url: str, cookies: dict[str, str], **kwargs: Any
) -> Response:
    """Send exactly these cookies, bypassing the client's jar."""
    c.cookies.clear()
    headers = dict(kwargs.pop("headers", {}))
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return await c.request(method, url, headers=headers, **kwargs)


async def _get(c: AsyncClient, jar: dict[str, str]) -> Response:
    return await _raw(c, "GET", ITEMS, {"session_id": jar["session_id"]})


def _assert_csrf_failed(r: Response) -> None:
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "csrf_failed"


def _assert_invalid_session(r: Response) -> None:
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "invalid_credentials"
    assert "www-authenticate" not in r.headers


# --- login --------------------------------------------------------------------


async def test_login_sets_session_cookie_httponly_samesite_lax(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("jar@example.com")
    r = await _login(anon_client, "jar@example.com")
    cookies = _set_cookies(r)
    assert set(cookies) == {"session_id", "csrf_token"}

    raw, session_attrs = cookies["session_id"]
    assert "httponly" in session_attrs
    assert session_attrs["path"] == "/"
    assert session_attrs["samesite"] == "lax"
    assert "secure" not in session_attrs  # dev default; non-dev requires COOKIE_SECURE
    assert "domain" not in session_attrs
    assert int(session_attrs["max-age"]) == ABSOLUTE_SECONDS

    csrf, csrf_attrs = cookies["csrf_token"]
    assert "httponly" not in csrf_attrs and csrf_attrs["path"] == "/"
    assert csrf_attrs["max-age"] == session_attrs["max-age"]

    # The body carries no secret: only what JS needs.
    body = r.json()
    assert set(body) == {"token_type", "expires_in", "csrf_token"}
    assert body["token_type"] == "session"
    assert body["expires_in"] == ABSOLUTE_SECONDS
    assert body["csrf_token"] == csrf
    assert raw not in r.text


async def test_session_cookie_attributes_follow_settings(
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
    cookies = _set_cookies(await _login(anon_client, "attrs@example.com"))
    assert set(cookies) == {"session_id", "csrf_token"}
    for _, attrs in cookies.values():
        assert "secure" in attrs
        assert attrs["samesite"] == "strict"
        assert attrs["domain"] == "example.test"


async def test_only_the_hash_of_the_session_id_is_stored(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user("hash@example.com")
    raw = _set_cookies(await _login(anon_client, "hash@example.com"))["session_id"][0]
    (row,) = await _rows(maker)
    assert row.user_id == user.id
    assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert len(raw) >= 43  # token_urlsafe(32)
    for value in (row.id, row.user_id, row.token_hash, row.created_at, row.expires_at):
        assert raw not in str(value)


async def test_wrong_password_sets_no_cookies_and_creates_no_session(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user("wrong@example.com")
    r = await anon_client.post(
        LOGIN,
        json={"email": "wrong@example.com", "password": "nope-nope-nope", "delivery": "session"},
    )
    assert r.status_code == 401
    assert "set-cookie" not in r.headers
    assert await _rows(maker) == []


async def test_delivery_values_follow_enabled_methods(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("deliv@example.com")
    creds = {"email": "deliv@example.com", "password": PASSWORD}
    assert (await anon_client.post(LOGIN, json={**creds, "delivery": "pigeon"})).status_code == 422
{%- if not bearer %}
    assert (await anon_client.post(LOGIN, json={**creds, "delivery": "bearer"})).status_code == 422
{%- endif %}
{%- if not jwt_cookie %}
    assert (await anon_client.post(LOGIN, json={**creds, "delivery": "cookie"})).status_code == 422
{%- endif %}
    default = await anon_client.post(LOGIN, json=creds)
    assert default.status_code == 200, default.text
{%- if bearer %}
    # Bearer is the first enabled method, so it is the default delivery.
    assert default.json()["token_type"] == "bearer"
    assert "set-cookie" not in default.headers
{%- elif jwt_cookie %}
    assert default.json()["token_type"] == "cookie"
{%- else %}
    assert default.json()["token_type"] == "session"
    assert "session_id" in _set_cookies(default)
{%- endif %}


async def test_session_cookie_settings_are_validated() -> None:
    # Outside dev the cookies must be Secure; SameSite=none requires Secure.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="prod", secret_key=STRONG)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="dev", secret_key=STRONG, cookie_samesite="none")
    Settings(_env_file=None, environment="prod", secret_key=STRONG, cookie_secure=True)


# --- authenticating with the session ---------------------------------------------


async def test_session_authenticates_requests(
    make_user: MakeUser, make_session_client: MakeSessionClient
) -> None:
    await make_user("reader@example.com")
    c = await make_session_client("reader@example.com", csrf_header=False)
    assert (await c.get(ITEMS)).status_code == 200
    me = await c.get(ME)
    assert me.status_code == 200
    assert me.json()["email"] == "reader@example.com"


async def test_unknown_session_cookie_401(anon_client: AsyncClient) -> None:
    _assert_invalid_session(await _raw(anon_client, "GET", ITEMS, {"session_id": "nope"}))


async def test_no_credentials_not_authenticated(anon_client: AsyncClient) -> None:
    r = await anon_client.get(ITEMS)
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
{%- if bearer %}
    assert r.headers["www-authenticate"] == "Bearer"
{%- else %}
    assert "www-authenticate" not in r.headers
{%- endif %}


async def test_openapi_advertises_session_cookie_scheme(anon_client: AsyncClient) -> None:
    for version in ("v1", "v2"):
        schema = (await anon_client.get(f"/api/{version}/openapi.json")).json()
        schemes = list(schema["components"]["securitySchemes"].values())
        assert {"type": "apiKey", "in": "cookie", "name": "session_id"} in schemes
{%- if bearer %}
        assert {"type": "http", "scheme": "bearer"} in schemes
{%- else %}
        assert not any(s.get("scheme") == "bearer" for s in schemes)
{%- endif %}


{%- if not bearer and not jwt_cookie %}


async def test_client_fixture_uses_session_login(client: AsyncClient) -> None:
    # Session-only build: ``client`` is the real session login plus the CSRF header.
    assert "authorization" not in client.headers
    assert client.headers[CSRF_HEADER] == client.cookies["csrf_token"]
    assert "session_id" in client.cookies
    assert (await client.post(ITEMS, json={"name": "via-fixture"})).status_code == 201
{%- endif %}


# --- expiry: idle timeout, sliding window, absolute lifetime ------------------------


async def test_idle_session_expired_401(
    anon_client: AsyncClient,
    make_user: MakeUser,
    clock: Clock,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await make_user("idle@example.com")
    jar = await _fresh_jar_login(anon_client, "idle@example.com")
    assert len(await _rows(maker)) == 1
    clock.advance(minutes=31)
    _assert_invalid_session(await _get(anon_client, jar))
    assert await _rows(maker) == []  # expired rows are deleted when encountered
    # The id stays dead.
    clock.now -= timedelta(minutes=30)
    _assert_invalid_session(await _get(anon_client, jar))


async def test_session_idle_timeout_is_configurable(
    anon_client: AsyncClient,
    make_user: MakeUser,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    await make_user("cfg@example.com")
    monkeypatch.setenv("SESSION_IDLE_TIMEOUT_MINUTES", "5")
    get_settings.cache_clear()
    jar = await _fresh_jar_login(anon_client, "cfg@example.com")
    clock.advance(minutes=4)
    assert (await _get(anon_client, jar)).status_code == 200
    clock.advance(minutes=6)
    _assert_invalid_session(await _get(anon_client, jar))


async def test_activity_slides_the_idle_window(
    anon_client: AsyncClient,
    make_user: MakeUser,
    clock: Clock,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await make_user("slide@example.com")
    jar = await _fresh_jar_login(anon_client, "slide@example.com")
    cookies = {"session_id": jar["session_id"]}
    started = clock.now

    clock.advance(minutes=20)
    assert (await _raw(anon_client, "GET", ITEMS, cookies)).status_code == 200
    (row,) = await _rows(maker)
    assert row.last_seen_at.replace(tzinfo=UTC) == started + timedelta(minutes=20)

    # 40 minutes after login but only 20 after the last use: still valid.
    clock.advance(minutes=20)
    assert (await _raw(anon_client, "GET", ITEMS, cookies)).status_code == 200
    (row,) = await _rows(maker)
    assert row.last_seen_at.replace(tzinfo=UTC) == started + timedelta(minutes=40)


async def test_last_seen_only_written_when_more_than_60_seconds_stale(
    anon_client: AsyncClient,
    make_user: MakeUser,
    clock: Clock,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await make_user("stale@example.com")
    jar = await _fresh_jar_login(anon_client, "stale@example.com")
    cookies = {"session_id": jar["session_id"]}
    started = clock.now

    clock.advance(seconds=59)
    assert (await _raw(anon_client, "GET", ITEMS, cookies)).status_code == 200
    (row,) = await _rows(maker)
    assert row.last_seen_at.replace(tzinfo=UTC) == started  # not written

    clock.advance(seconds=2)  # 61 seconds since last_seen_at
    assert (await _raw(anon_client, "GET", ITEMS, cookies)).status_code == 200
    (row,) = await _rows(maker)
    assert row.last_seen_at.replace(tzinfo=UTC) == started + timedelta(seconds=61)


async def test_session_past_absolute_lifetime_401(
    anon_client: AsyncClient,
    make_user: MakeUser,
    clock: Clock,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    await make_user("absolute@example.com")
    monkeypatch.setenv("SESSION_IDLE_TIMEOUT_MINUTES", "100000")  # isolate the absolute limit
    get_settings.cache_clear()
    jar = await _fresh_jar_login(anon_client, "absolute@example.com")
    cookies = {"session_id": jar["session_id"]}
    clock.advance(hours=11, minutes=59)
    assert (await _raw(anon_client, "GET", ITEMS, cookies)).status_code == 200
    clock.advance(minutes=1)  # exactly the absolute lifetime: no longer valid
    _assert_invalid_session(await _raw(anon_client, "GET", ITEMS, cookies))
    assert await _rows(maker) == []


async def test_activity_cannot_extend_the_absolute_lifetime(
    anon_client: AsyncClient, make_user: MakeUser, clock: Clock
) -> None:
    await make_user("keepalive@example.com")
    jar = await _fresh_jar_login(anon_client, "keepalive@example.com")
    cookies = {"session_id": jar["session_id"]}
    steps = ABSOLUTE_SECONDS // (20 * 60)  # a request every 20 minutes keeps it idle-fresh
    for _ in range(steps - 1):
        clock.advance(minutes=20)
        assert (await _raw(anon_client, "GET", ITEMS, cookies)).status_code == 200
    clock.advance(minutes=20)  # now == login + 12h
    _assert_invalid_session(await _raw(anon_client, "GET", ITEMS, cookies))


# --- fixation, logout ----------------------------------------------------------------


async def test_login_rotates_session_id(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user("rotate@example.com")
    first = await _login(anon_client, "rotate@example.com")
    old = _set_cookies(first)["session_id"][0]
    assert anon_client.cookies["session_id"] == old

    # Logging in again while presenting the old session id (fixation attempt).
    second = await _login(anon_client, "rotate@example.com")
    new = _set_cookies(second)["session_id"][0]
    assert new != old
    rows = await _rows(maker)
    assert [r.token_hash for r in rows] == [hashlib.sha256(new.encode()).hexdigest()]
    _assert_invalid_session(await _raw(anon_client, "GET", ITEMS, {"session_id": old}))
    assert (await _raw(anon_client, "GET", ITEMS, {"session_id": new})).status_code == 200


async def test_login_never_adopts_a_presented_session_id(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user("fixate@example.com")
    attacker_chosen = "attacker-chosen-session-id"
    r = await anon_client.post(
        LOGIN,
        json={"email": "fixate@example.com", "password": PASSWORD, "delivery": "session"},
        headers={"Cookie": f"session_id={attacker_chosen}"},
    )
    assert r.status_code == 200, r.text
    assert _set_cookies(r)["session_id"][0] != attacker_chosen
    assert len(await _rows(maker)) == 1


async def test_logout_deletes_session(
    make_user: MakeUser,
    make_session_client: MakeSessionClient,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await make_user("bye@example.com")
    c = await make_session_client("bye@example.com")
    old = c.cookies["session_id"]
    assert len(await _rows(maker)) == 1

    r = await c.post(LOGOUT)
    assert r.status_code == 204, r.text
    assert await _rows(maker) == []
    cleared = _set_cookies(r)
    assert set(cleared) == {"session_id", "csrf_token"}
    for name, (value, attrs) in cleared.items():
        assert value == ""
        assert attrs["max-age"] == "0"
        assert attrs["path"] == "/"
        assert attrs["samesite"] == "lax"
        assert ("httponly" in attrs) == (name == "session_id")
    # Presenting the old id afterwards fails.
    c.cookies.clear()
    _assert_invalid_session(await _raw(c, "GET", ITEMS, {"session_id": old}))


async def test_logout_requires_csrf(
    make_user: MakeUser,
    make_session_client: MakeSessionClient,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await make_user("csrf-out@example.com")
    c = await make_session_client("csrf-out@example.com", csrf_header=False)
    _assert_csrf_failed(await c.post(LOGOUT))
    assert len(await _rows(maker)) == 1  # a forged logout did nothing


async def test_logout_is_idempotent_for_unknown_or_expired_sessions(
    anon_client: AsyncClient, make_user: MakeUser, clock: Clock
) -> None:
    r = await _raw(anon_client, "POST", LOGOUT, {"session_id": "never-issued"})
    assert r.status_code == 204
    assert set(_set_cookies(r)) == {"session_id", "csrf_token"}

    assert (await anon_client.post(LOGOUT)).status_code == 204  # no cookie at all

    await make_user("gone@example.com")
    jar = await _fresh_jar_login(anon_client, "gone@example.com")
    clock.advance(hours=13)
    r = await _raw(anon_client, "POST", LOGOUT, {"session_id": jar["session_id"]})
    assert r.status_code == 204


async def test_two_sessions_of_one_user_are_independent(
    make_user: MakeUser,
    make_session_client: MakeSessionClient,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await make_user("twice@example.com")
    first = await make_session_client("twice@example.com")
    second = await make_session_client("twice@example.com")
    assert first.cookies["session_id"] != second.cookies["session_id"]
    assert len(await _rows(maker)) == 2

    assert (await first.post(LOGOUT)).status_code == 204
    assert len(await _rows(maker)) == 1
    assert (await second.get(ITEMS)).status_code == 200
    first.cookies.clear()
    assert (await first.get(ITEMS)).status_code == 401


async def test_deactivated_user_session_401_and_row_deleted(
    anon_client: AsyncClient,
    make_user: MakeUser,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    user = await make_user("deact@example.com")
    jar = await _fresh_jar_login(anon_client, "deact@example.com")
    cookies = {"session_id": jar["session_id"]}
    assert (await _raw(anon_client, "GET", ITEMS, cookies)).status_code == 200
    async with maker() as db:
        await db.execute(update(User).where(User.id == user.id).values(is_active=False))
        await db.commit()
    _assert_invalid_session(await _raw(anon_client, "GET", ITEMS, cookies))
    assert await _rows(maker) == []


# --- CSRF ---------------------------------------------------------------------------


async def test_session_post_requires_csrf(
    make_user: MakeUser, make_session_client: MakeSessionClient
) -> None:
    await make_user("nocsrf@example.com")
    c = await make_session_client("nocsrf@example.com", csrf_header=False)
    _assert_csrf_failed(await c.post(ITEMS, json={"name": "x"}))
    created_item = await c.get(ITEMS)  # GET is exempt
    assert created_item.status_code == 200


async def test_session_unsafe_methods_with_valid_csrf_ok(
    make_user: MakeUser, make_session_client: MakeSessionClient
) -> None:
    await make_user("writer@example.com")
    c = await make_session_client("writer@example.com")
    created = await c.post(ITEMS, json={"name": "a"})
    assert created.status_code == 201, created.text
    item = created.json()["id"]
    assert (await c.patch(f"{ITEMS}/{item}", json={"name": "b"})).status_code == 200
    assert (await c.delete(f"{ITEMS}/{item}")).status_code == 204


async def test_session_csrf_header_must_match_cookie_and_be_signed(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("mismatch@example.com")
    await make_user("other@example.com")
    jar = await _fresh_jar_login(anon_client, "mismatch@example.com")
    other = await _fresh_jar_login(anon_client, "other@example.com")
    cookies = {"session_id": jar["session_id"], "csrf_token": jar["csrf_token"]}
    payload = {"json": {"name": "x"}}

    for bad in ("garbage", "", other["csrf"]):
        r = await _raw(anon_client, "POST", ITEMS, cookies, headers={CSRF_HEADER: bad}, **payload)
        _assert_csrf_failed(r)
    # Header present, cookie missing.
    r = await _raw(
        anon_client, "POST", ITEMS, {"session_id": jar["session_id"]},
        headers={CSRF_HEADER: jar["csrf"]}, **payload,
    )
    _assert_csrf_failed(r)
    ok = await _raw(
        anon_client, "POST", ITEMS, cookies, headers={CSRF_HEADER: jar["csrf"]}, **payload
    )
    assert ok.status_code == 201, ok.text


async def test_session_of_user_a_rejects_csrf_token_of_user_b(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("alice@example.com")
    await make_user("bob@example.com")
    alice = await _fresh_jar_login(anon_client, "alice@example.com")
    bob = await _fresh_jar_login(anon_client, "bob@example.com")
    # Bob's CSRF token is perfectly valid (cookie == header, signed), but for Bob.
    r = await _raw(
        anon_client, "POST", ITEMS,
        {"session_id": alice["session_id"], "csrf_token": bob["csrf_token"]},
        headers={CSRF_HEADER: bob["csrf"]}, json={"name": "x"},
    )
    _assert_csrf_failed(r)


async def test_invalid_session_is_rejected_before_csrf(anon_client: AsyncClient) -> None:
    r = await _raw(anon_client, "POST", ITEMS, {"session_id": "nope"}, json={"name": "x"})
    _assert_invalid_session(r)  # 401, not 403: the session is validated first
{%- if jwt_cookie %}


# --- interplay with the JWT cookie method ------------------------------------------------


async def test_invalid_session_cookie_does_not_fall_through_to_access_cookie(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("both@example.com")
    r = await anon_client.post(
        LOGIN, json={"email": "both@example.com", "password": PASSWORD, "delivery": "cookie"}
    )
    access = _set_cookies(r)["access_token"][0]
    ok = await _raw(anon_client, "GET", ITEMS, {"access_token": access})
    assert ok.status_code == 200, ok.text
    r = await _raw(anon_client, "GET", ITEMS, {"access_token": access, "session_id": "bogus"})
    _assert_invalid_session(r)


async def test_session_wins_over_access_token_cookie(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("wins@example.com")
    jar = await _fresh_jar_login(anon_client, "wins@example.com")
    r = await _raw(
        anon_client, "GET", ITEMS,
        {"session_id": jar["session_id"], "access_token": "not-a-jwt"},
    )
    assert r.status_code == 200, r.text
{%- endif %}
