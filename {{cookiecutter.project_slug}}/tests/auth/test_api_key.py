{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{%- set basic = cookiecutter.use_auth_basic == "y" -%}
{#- How the tests act as a "human" (a non-API-key principal) in this build. -#}
{#- The pre-gen hook guarantees one of them is on when API keys are. -#}
{%- set human = "bearer" if bearer else ("cookie" if cookie else ("session" if session else "basic")) -%}
import hashlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import {% if human not in ("cookie", "session") %}ASGITransport, {% endif %}AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ApiKey, User
{%- set names = ["MakeApiKey", "MakeUser"] -%}
{%- if human == "cookie" %}{% set _ = names.append("MakeCookieClient") %}{% endif -%}
{%- if human == "session" %}{% set _ = names.append("MakeSessionClient") %}{% endif -%}
{%- if human == "basic" %}{% set _ = names.append("basic_header") %}{% endif %}
from tests.conftest import {{ names | sort(case_sensitive=true) | join(", ") }}

KEYS = "/api/v1/api-keys"
ITEMS = "/api/v1/items"
ME = "/api/v1/users/me"
USERS = "/api/v1/users"
KEY_FORMAT = re.compile(r"^[0-9a-f]{8}\.[A-Za-z0-9_-]{43}$")


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


def _x(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@dataclass
class Human:
    """A user plus a client that acts as that user through a non-API-key method."""

    user: User
    client: AsyncClient


MakeHuman = Callable[..., Awaitable[Human]]


@pytest_asyncio.fixture
async def make_human(
    application: FastAPI,
    make_user: MakeUser,
{%- if human == "cookie" %}
    make_cookie_client: MakeCookieClient,
{%- elif human == "session" %}
    make_session_client: MakeSessionClient,
{%- elif human == "bearer" %}
    auth_headers: Callable[[User], dict[str, str]],
{%- endif %}
) -> AsyncIterator[MakeHuman]:
    clients: list[AsyncClient] = []

    async def _make(email: str = "owner@example.com", **user_kwargs: Any) -> Human:
        user = await make_user(email, **user_kwargs)
{%- if human == "cookie" %}
        c = await make_cookie_client(email)
{%- elif human == "session" %}
        c = await make_session_client(email)
{%- else %}
{%- if human == "bearer" %}
        headers = auth_headers(user)
{%- else %}
        headers = basic_header(email)
{%- endif %}
        c = AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test", headers=headers
        )
        clients.append(c)
{%- endif %}
        return Human(user, c)

    yield _make
    for c in clients:
        await c.aclose()


async def _create(human: Human, name: str = "ci", **body: Any) -> dict[str, Any]:
    r = await human.client.post(KEYS, json={"name": name, **body})
    assert r.status_code == 201, r.text
    created: dict[str, Any] = r.json()
    return created


async def _row(maker: async_sessionmaker[AsyncSession], key_id: str) -> ApiKey:
    async with maker() as s:
        return (await s.scalars(select(ApiKey).where(ApiKey.id == UUID(key_id)))).one()


# --- creating keys ----------------------------------------------------------------


async def test_api_key_created_shown_once_stored_hashed(
    make_human: MakeHuman, maker: async_sessionmaker[AsyncSession]
) -> None:
    owner = await make_human()
    created = await _create(owner, "deploy", expires_in_days=30)
    raw = created["key"]
    assert KEY_FORMAT.match(raw)
    prefix, secret = raw.split(".")
    assert created["prefix"] == prefix
    assert created["name"] == "deploy"
    assert created["expires_at"] is not None
    assert set(created) == {"id", "name", "prefix", "key", "expires_at", "created_at"}

    row = await _row(maker, created["id"])
    assert row.key_hash == hashlib.sha256(secret.encode()).hexdigest()
    assert row.prefix == prefix
    assert row.user_id == owner.user.id
    # The raw key (and its secret) is in no column of the row.
    assert all(secret not in str(v) and raw not in str(v) for v in vars(row).values())

    # ... and in no later response either.
    listed = await owner.client.get(KEYS)
    assert secret not in listed.text and raw not in listed.text


async def test_prefix_is_8_hex_and_unique(
    make_human: MakeHuman, maker: async_sessionmaker[AsyncSession]
) -> None:
    owner = await make_human()
    keys = [await _create(owner, f"k{i}") for i in range(5)]
    prefixes = {k["prefix"] for k in keys}
    assert len(prefixes) == 5
    assert all(re.fullmatch(r"[0-9a-f]{8}", p) for p in prefixes)
    async with maker() as s:
        assert len((await s.scalars(select(ApiKey.prefix))).all()) == 5


async def test_prefix_collision_is_retried(
    make_human: MakeHuman, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.api_key as service_module

    owner = await make_human()
    first = await _create(owner, "first")
    real = service_module.secrets.token_hex
    calls = iter([first["prefix"], first["prefix"]])
    monkeypatch.setattr(
        service_module.secrets, "token_hex", lambda n: next(calls, None) or real(n)
    )
    second = await _create(owner, "second")
    assert second["prefix"] != first["prefix"]


async def test_create_validates_input(make_human: MakeHuman) -> None:
    owner = await make_human()
    bad_bodies: list[dict[str, Any]] = [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": "ok", "expires_in_days": 0},
        {"name": "ok", "expires_in_days": 3651},
        {"name": "ok", "expires_in_days": "soon"},
    ]
    for body in bad_bodies:
        assert (await owner.client.post(KEYS, json=body)).status_code == 422, body
    ok = await owner.client.post(KEYS, json={"name": "x" * 100, "expires_in_days": 3650})
    assert ok.status_code == 201


async def test_key_without_expiry_has_null_expires_at(make_human: MakeHuman) -> None:
    owner = await make_human()
    assert (await _create(owner))["expires_at"] is None


async def test_endpoints_require_authentication(anon_client: AsyncClient) -> None:
    assert (await anon_client.post(KEYS, json={"name": "x"})).status_code == 401
    assert (await anon_client.get(KEYS)).status_code == 401
    assert (await anon_client.delete(f"{KEYS}/{uuid4()}")).status_code == 401


# --- authenticating with a key -----------------------------------------------------


async def test_api_key_authenticates_with_owner_permissions(
    anon_client: AsyncClient, make_user: MakeUser, make_api_key: MakeApiKey
) -> None:
    member = await make_user("member2@example.com")
    admin = await make_user("admin@example.com", superuser=True)
    member_key = await make_api_key(member)
    admin_key = await make_api_key(admin)

    me = await anon_client.get(ME, headers=_x(member_key))
    assert me.status_code == 200
    assert me.json()["email"] == "member2@example.com"
    assert (await anon_client.get(ITEMS, headers=_x(member_key))).status_code == 200
    # The key is exactly as powerful as its owner: no more, no less.
    assert (await anon_client.get(USERS, headers=_x(member_key))).status_code == 403
    assert (await anon_client.get(USERS, headers=_x(admin_key))).status_code == 200


async def test_post_with_api_key_needs_no_csrf(
    anon_client: AsyncClient, make_user: MakeUser, make_api_key: MakeApiKey
) -> None:
    key = await make_api_key(await make_user("nocsrf@example.com"))
    r = await anon_client.post(ITEMS, json={"name": "via key"}, headers=_x(key))
    assert r.status_code == 201


async def test_unknown_prefix_and_wrong_secret_give_the_same_401(
    anon_client: AsyncClient, make_user: MakeUser, make_api_key: MakeApiKey
) -> None:
    key = await make_api_key(await make_user("same@example.com"))
    prefix, _, secret = key.partition(".")
    wrong_secret = f"{prefix}.{'A' * len(secret)}"
    unknown_prefix = f"{'0' * 8}.{secret}"
    no_dot = prefix
    bodies = []
    for bad in (wrong_secret, unknown_prefix, no_dot, "", "x" * 5000):
        r = await anon_client.get(ME, headers=_x(bad))
        assert r.status_code == 401, bad
        assert r.json()["code"] == "invalid_credentials"
        assert "www-authenticate" not in r.headers  # that challenge is Bearer's alone
        bodies.append({k: v for k, v in r.json().items() if k != "request_id"})
    assert all(b == bodies[0] for b in bodies)


async def test_unknown_prefix_still_compares_against_a_dummy_hash(
    anon_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.api_key as service_module

    compared: list[tuple[str, str]] = []
    real = service_module.hmac.compare_digest

    def spy(a: str | bytes, b: str | bytes) -> bool:
        compared.append((str(a), str(b)))
        return real(a, b)

    monkeypatch.setattr(service_module.hmac, "compare_digest", spy)
    r = await anon_client.get(ME, headers=_x(f"{'0' * 8}.{'s' * 43}"))
    assert r.status_code == 401
    assert len(compared) == 1


async def test_revoked_key_401(make_human: MakeHuman, anon_client: AsyncClient) -> None:
    owner = await make_human()
    created = await _create(owner)
    assert (await anon_client.get(ME, headers=_x(created["key"]))).status_code == 200
    assert (await owner.client.delete(f"{KEYS}/{created['id']}")).status_code == 204
    r = await anon_client.get(ME, headers=_x(created["key"]))
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_expired_key_401(
    make_human: MakeHuman, anon_client: AsyncClient, clock: Clock
) -> None:
    owner = await make_human()
    created = await _create(owner, expires_in_days=1)
    expires_at = clock.now + timedelta(days=1)
    clock.now = expires_at - timedelta(microseconds=1)
    assert (await anon_client.get(ME, headers=_x(created["key"]))).status_code == 200
    clock.now = expires_at  # the boundary itself is already expired
    r = await anon_client.get(ME, headers=_x(created["key"]))
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_key_without_expiry_keeps_working(
    make_human: MakeHuman, anon_client: AsyncClient, clock: Clock
) -> None:
    owner = await make_human()
    created = await _create(owner)
    clock.advance(days=3650)
    assert (await anon_client.get(ME, headers=_x(created["key"]))).status_code == 200


async def test_inactive_owner_401(
    anon_client: AsyncClient,
    make_user: MakeUser,
    make_api_key: MakeApiKey,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    user = await make_user("gone@example.com")
    key = await make_api_key(user)
    assert (await anon_client.get(ME, headers=_x(key))).status_code == 200
    async with maker() as s:
        await s.execute(update(User).where(User.id == user.id).values(is_active=False))
        await s.commit()
    r = await anon_client.get(ME, headers=_x(key))
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_deleting_the_owner_removes_the_key(
    anon_client: AsyncClient,
    make_user: MakeUser,
    make_api_key: MakeApiKey,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    user = await make_user("deleted@example.com")
    key = await make_api_key(user)
    async with maker() as s:
        await s.delete(await s.get(User, user.id))
        await s.commit()
    assert (await anon_client.get(ME, headers=_x(key))).status_code == 401


async def test_last_used_at_is_updated_at_most_once_a_minute(
    anon_client: AsyncClient,
    make_human: MakeHuman,
    clock: Clock,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await make_human()
    created = await _create(owner)
    key = created["key"]
    assert (await _row(maker, created["id"])).last_used_at is None

    first_use = clock.now
    assert (await anon_client.get(ME, headers=_x(key))).status_code == 200
    stamp = await _row(maker, created["id"])
    assert stamp.last_used_at is not None

    clock.advance(seconds=59)
    assert (await anon_client.get(ME, headers=_x(key))).status_code == 200
    assert (await _row(maker, created["id"])).last_used_at == stamp.last_used_at

    clock.now = first_use + timedelta(seconds=60)
    assert (await anon_client.get(ME, headers=_x(key))).status_code == 200
    later = (await _row(maker, created["id"])).last_used_at
    assert later is not None and later != stamp.last_used_at
    assert later.replace(tzinfo=UTC) == first_use + timedelta(seconds=60)


async def test_failed_authentication_does_not_touch_last_used_at(
    anon_client: AsyncClient, make_human: MakeHuman, maker: async_sessionmaker[AsyncSession]
) -> None:
    owner = await make_human()
    created = await _create(owner)
    prefix, _, secret = created["key"].partition(".")
    r = await anon_client.get(ME, headers=_x(f"{prefix}.{'B' * len(secret)}"))
    assert r.status_code == 401
    assert (await _row(maker, created["id"])).last_used_at is None


# --- managing keys -------------------------------------------------------------------


async def test_api_key_cannot_create_list_or_revoke_keys(
    make_human: MakeHuman, anon_client: AsyncClient
) -> None:
    owner = await make_human()
    created = await _create(owner)
    headers = _x(created["key"])
    for r in (
        await anon_client.post(KEYS, json={"name": "minted"}, headers=headers),
        await anon_client.get(KEYS, headers=headers),
        await anon_client.delete(f"{KEYS}/{created['id']}", headers=headers),
    ):
        assert r.status_code == 403
        assert r.json()["code"] == "api_key_not_allowed"
    # Nothing was revoked or created by those attempts.
    assert (await anon_client.get(ME, headers=headers)).status_code == 200
    assert len((await owner.client.get(KEYS)).json()["items"]) == 1


async def test_list_returns_only_own_keys_without_secrets(
    make_human: MakeHuman, maker: async_sessionmaker[AsyncSession]
) -> None:
    alice = await make_human("alice@example.com")
    bob = await make_human("bob@example.com")
    mine = await _create(alice, "mine")
    theirs = await _create(bob, "theirs")

    body = (await alice.client.get(KEYS)).json()
    assert body["total"] == 1
    [item] = body["items"]
    assert item["id"] == mine["id"]
    assert set(item) == {
        "id", "name", "prefix", "created_at", "expires_at", "revoked_at", "last_used_at"
    }
    row = await _row(maker, mine["id"])
    text = (await alice.client.get(KEYS)).text
    assert row.key_hash not in text
    assert mine["key"] not in text and theirs["key"] not in text
    assert theirs["id"] not in text


async def test_revoking_another_users_key_is_404(
    make_human: MakeHuman, anon_client: AsyncClient
) -> None:
    alice = await make_human("alice@example.com")
    bob = await make_human("bob@example.com")
    theirs = await _create(bob, "theirs")
    r = await alice.client.delete(f"{KEYS}/{theirs['id']}")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    assert (await alice.client.delete(f"{KEYS}/{uuid4()}")).status_code == 404
    assert (await alice.client.delete(f"{KEYS}/not-a-uuid")).status_code == 422
    # Bob's key is untouched.
    assert (await anon_client.get(ME, headers=_x(theirs["key"]))).status_code == 200


async def test_revoking_twice_is_idempotent(
    make_human: MakeHuman, maker: async_sessionmaker[AsyncSession], clock: Clock
) -> None:
    owner = await make_human()
    created = await _create(owner)
    assert (await owner.client.delete(f"{KEYS}/{created['id']}")).status_code == 204
    revoked_at = (await _row(maker, created["id"])).revoked_at
    assert revoked_at is not None
    clock.advance(minutes=5)  # stays inside a session-based human's idle window
    assert (await owner.client.delete(f"{KEYS}/{created['id']}")).status_code == 204
    assert (await _row(maker, created["id"])).revoked_at == revoked_at
    listed = (await owner.client.get(KEYS)).json()["items"]
    assert listed[0]["revoked_at"] is not None


async def test_at_most_20_active_keys_per_user(
    make_human: MakeHuman, maker: async_sessionmaker[AsyncSession]
) -> None:
    owner = await make_human()
    other = await make_human("other@example.com")
    ids = [(await _create(owner, f"k{i}", expires_in_days=1))["id"] for i in range(20)]
    over = await owner.client.post(KEYS, json={"name": "21st"})
    assert over.status_code == 409
    assert over.json()["code"] == "api_key_limit"
    # Other users have their own budget.
    await _create(other, "fine")
    # A revoked key frees a slot ...
    assert (await owner.client.delete(f"{KEYS}/{ids[0]}")).status_code == 204
    await _create(owner, "again", expires_in_days=1)
    assert (await owner.client.post(KEYS, json={"name": "21st"})).status_code == 409
    # ... and so do expired ones (moved into the past directly: the owner's own login
    # session, in session builds, would not survive the clock moving that far).
    async with maker() as s:
        await s.execute(
            update(ApiKey)
            .where(ApiKey.user_id == owner.user.id)
            .values(expires_at=datetime.now(UTC) - timedelta(days=1))
        )
        await s.commit()
    assert (await owner.client.post(KEYS, json={"name": "fresh"})).status_code == 201
