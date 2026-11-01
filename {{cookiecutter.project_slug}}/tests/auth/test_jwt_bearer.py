import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.user as user_service_module
from app.core.config import get_settings
from app.models import RefreshToken, User
from app.repositories.refresh_token import RefreshTokenRepository
from tests.conftest import MakeUser

PASSWORD = "correct-horse-battery"
Headers = Callable[[User], dict[str, str]]


def _b64(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _claims(user: User, **overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user.id),
        "jti": uuid.uuid4().hex,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _mint(claims: dict[str, Any], algorithm: str = "HS256", key: str | None = None) -> str:
    return jwt.encode(claims, key or get_settings().secret_key, algorithm=algorithm)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(c: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def _assert_rejected(c: AsyncClient, token: str) -> None:
    r = await c.get("/api/v1/items", headers=_bearer(token))
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "invalid_credentials"
    assert r.headers["www-authenticate"].startswith("Bearer")


# --- login -----------------------------------------------------------------


async def test_login_returns_access_and_refresh(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("alice@example.com")
    body = await _login(anon_client, "alice@example.com")
    assert set(body) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().access_token_expire_minutes * 60
    claims = jwt.decode(
        body["access_token"], get_settings().secret_key, algorithms=["HS256"]
    )
    assert claims["type"] == "access"
    assert {"sub", "jti", "iat", "exp"} <= set(claims)
    r = await anon_client.get("/api/v1/items", headers=_bearer(body["access_token"]))
    assert r.status_code == 200


async def test_login_email_is_case_insensitive(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("bob@example.com")
    await _login(anon_client, "BOB@Example.com")


async def test_login_failures_are_indistinguishable(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("carol@example.com")
    await make_user("inactive@example.com", active=False)
    attempts = [
        ("nobody@example.com", PASSWORD),
        ("carol@example.com", "wrong-password"),
        ("inactive@example.com", PASSWORD),
    ]
    bodies = []
    for email, password in attempts:
        r = await anon_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert r.status_code == 401
        body = r.json()
        bodies.append((body["code"], body["detail"]))
    assert bodies[0][0] == "invalid_credentials"
    assert len(set(bodies)) == 1


async def test_inactive_user_cannot_login(anon_client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("sleepy@example.com", active=False)
    r = await anon_client.post(
        "/api/v1/auth/login", json={"email": "sleepy@example.com", "password": PASSWORD}
    )
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_unknown_email_still_runs_password_verification(
    anon_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = user_service_module.verify_password

    def spy(password: str, hashed: str) -> bool:
        calls.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(user_service_module, "verify_password", spy)
    r = await anon_client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}
    )
    assert r.status_code == 401
    assert len(calls) == 1
    assert calls[0].startswith("$argon2")


# --- bearer authenticator ----------------------------------------------------


async def test_bearer_gets_items_without_token_401(anon_client: AsyncClient) -> None:
    for version in ("v1", "v2"):
        r = await anon_client.get(f"/api/{version}/items")
        assert r.status_code == 401
        body = r.json()
        assert body["code"] == "not_authenticated"
        assert "request_id" in body
        assert r.headers["www-authenticate"] == "Bearer"


async def test_items_require_auth_for_writes_v1_and_v2(anon_client: AsyncClient) -> None:
    for version in ("v1", "v2"):
        r = await anon_client.post(f"/api/{version}/items", json={"name": "x"})
        assert r.status_code == 401


async def test_authenticated_client_can_use_items_v1_and_v2(client: AsyncClient) -> None:
    created = await client.post("/api/v1/items", json={"name": "mine"})
    assert created.status_code == 201
    assert (await client.get(f"/api/v2/items/{created.json()['id']}")).status_code == 200


async def test_health_stays_public(anon_client: AsyncClient) -> None:
    assert (await anon_client.get("/health/live")).status_code == 200
    assert (await anon_client.get("/health/ready")).status_code == 200


async def test_other_authorization_scheme_is_not_authenticated(
    anon_client: AsyncClient,
) -> None:
    # A scheme that no enabled method owns.
    headers = {"Authorization": "Negotiate Zm9vOmJhcg=="}
    r = await anon_client.get("/api/v1/items", headers=headers)
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


async def test_empty_bearer_token_rejected(anon_client: AsyncClient) -> None:
    r = await anon_client.get("/api/v1/items", headers={"Authorization": "Bearer "})
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_expired_access_token_401(anon_client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()
    past = datetime.now(UTC) - timedelta(minutes=10)
    token = _mint(_claims(user, iat=past, exp=past + timedelta(minutes=1)))
    await _assert_rejected(anon_client, token)


async def test_tampered_signature_401(anon_client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()
    head, payload, sig = _mint(_claims(user)).split(".")
    other = _b64({**_claims(user), "sub": str(uuid.uuid4()), "iat": 0, "exp": 2**31})
    await _assert_rejected(anon_client, f"{head}.{other}.{sig}")
    flipped = sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    await _assert_rejected(anon_client, f"{head}.{payload}.{flipped}")
    await _assert_rejected(anon_client, _mint(_claims(user), key="another-secret-" + "z" * 32))


async def test_alg_none_and_other_algorithms_rejected(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    body = _b64({**_claims(user), "iat": exp - 300, "exp": exp})
    for alg in ("none", "None", "NONE"):
        await _assert_rejected(anon_client, f"{_b64({'alg': alg, 'typ': 'JWT'})}.{body}.")
    for alg in ("HS384", "HS512"):
        await _assert_rejected(anon_client, _mint(_claims(user), algorithm=alg))


async def test_non_access_token_type_rejected(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user("typed@example.com")
    await _assert_rejected(anon_client, _mint(_claims(user, type="refresh")))
    no_type = _claims(user)
    del no_type["type"]
    await _assert_rejected(anon_client, _mint(no_type))
    # The opaque refresh token is not a JWT and must not work as a bearer token.
    pair = await _login(anon_client, "typed@example.com")
    await _assert_rejected(anon_client, pair["refresh_token"])


async def test_missing_required_claims_rejected(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    for claim in ("sub", "jti", "exp"):
        claims = _claims(user)
        del claims[claim]
        await _assert_rejected(anon_client, _mint(claims))
    await _assert_rejected(anon_client, _mint(_claims(user, sub="not-a-uuid")))


async def test_token_for_unknown_user_rejected(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    await _assert_rejected(anon_client, _mint(_claims(user, sub=str(uuid.uuid4()))))


async def test_deactivated_user_access_token_rejected(
    anon_client: AsyncClient,
    make_user: MakeUser,
    auth_headers: Headers,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    user = await make_user()
    headers = auth_headers(user)
    assert (await anon_client.get("/api/v1/items", headers=headers)).status_code == 200
    async with maker() as session:
        await session.execute(update(User).where(User.id == user.id).values(is_active=False))
        await session.commit()
    r = await anon_client.get("/api/v1/items", headers=headers)
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_openapi_advertises_bearer_scheme(anon_client: AsyncClient) -> None:
    for version in ("v1", "v2"):
        schema = (await anon_client.get(f"/api/{version}/openapi.json")).json()
        schemes = schema["components"]["securitySchemes"]
        assert {"type": "http", "scheme": "bearer"} in list(schemes.values())
        assert schema["paths"]["/items"]["get"]["security"]


# --- refresh tokens ----------------------------------------------------------


async def _refresh(c: AsyncClient, token: str) -> Any:
    return await c.post("/api/v1/auth/refresh", json={"refresh_token": token})


async def test_refresh_token_stored_only_as_sha256(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user("hashme@example.com")
    pair = await _login(anon_client, "hashme@example.com")
    async with maker() as session:
        row = (await session.scalars(select(RefreshToken))).one()
    assert row.token_hash == hashlib.sha256(pair["refresh_token"].encode()).hexdigest()
    assert row.revoked_at is None


async def test_refresh_rotates_and_old_token_rejected(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("rot@example.com")
    first = await _login(anon_client, "rot@example.com")
    r = await _refresh(anon_client, first["refresh_token"])
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["token_type"] == "bearer"
    authed = await anon_client.get("/api/v1/items", headers=_bearer(second["access_token"]))
    assert authed.status_code == 200
    old = await _refresh(anon_client, first["refresh_token"])
    assert old.status_code == 401
    assert old.json()["code"] == "invalid_credentials"


async def test_reused_refresh_token_revokes_all_user_tokens(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user("victim@example.com")
    await make_user("bystander@example.com")
    device_a = await _login(anon_client, "victim@example.com")
    device_b = await _login(anon_client, "victim@example.com")
    other = await _login(anon_client, "bystander@example.com")
    rotated = (await _refresh(anon_client, device_a["refresh_token"])).json()

    assert (await _refresh(anon_client, device_a["refresh_token"])).status_code == 401

    for token in (rotated["refresh_token"], device_b["refresh_token"]):
        assert (await _refresh(anon_client, token)).status_code == 401
    assert (await _refresh(anon_client, other["refresh_token"])).status_code == 200
    async with maker() as session:
        victim = (
            await session.scalars(select(User).where(User.email == "victim@example.com"))
        ).one()
        active = (
            await session.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == victim.id, RefreshToken.revoked_at.is_(None)
                )
            )
        ).all()
    assert active == []


async def test_expired_refresh_token_rejected(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user("old@example.com")
    pair = await _login(anon_client, "old@example.com")
    async with maker() as session:
        await session.execute(
            update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    r = await _refresh(anon_client, pair["refresh_token"])
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_unknown_refresh_token_rejected(anon_client: AsyncClient) -> None:
    r = await _refresh(anon_client, "never-issued")
    assert r.status_code == 401


async def test_refresh_rejected_for_deactivated_user(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user("gone@example.com")
    pair = await _login(anon_client, "gone@example.com")
    async with maker() as session:
        await session.execute(update(User).where(User.id == user.id).values(is_active=False))
        await session.commit()
    assert (await _refresh(anon_client, pair["refresh_token"])).status_code == 401


async def test_logout_revokes_refresh_token(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("bye@example.com")
    pair = await _login(anon_client, "bye@example.com")
    body = {"refresh_token": pair["refresh_token"]}
    assert (await anon_client.post("/api/v1/auth/logout", json=body)).status_code == 204
    assert (await anon_client.post("/api/v1/auth/logout", json=body)).status_code == 204
    unknown = {"refresh_token": "never-issued"}
    assert (await anon_client.post("/api/v1/auth/logout", json=unknown)).status_code == 204
    assert (await _refresh(anon_client, pair["refresh_token"])).status_code == 401


async def test_concurrent_double_refresh_only_one_succeeds(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user("race@example.com")
    pair = await _login(anon_client, "race@example.com")
    token_hash = hashlib.sha256(pair["refresh_token"].encode()).hexdigest()
    now = datetime.now(UTC)
    # Two sessions racing on the same token: the conditional UPDATE lets exactly one win.
    async with maker() as s1, maker() as s2:
        won_1 = await RefreshTokenRepository(s1).revoke_if_active(token_hash, now)
        await s1.commit()
        won_2 = await RefreshTokenRepository(s2).revoke_if_active(token_hash, now)
        await s2.commit()
    assert [won_1, won_2] == [True, False]


async def test_double_refresh_over_http_second_is_rejected(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("twice@example.com")
    pair = await _login(anon_client, "twice@example.com")
    results = [
        (await _refresh(anon_client, pair["refresh_token"])).status_code for _ in range(2)
    ]
    assert results == [200, 401]
