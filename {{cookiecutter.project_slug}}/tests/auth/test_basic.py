{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
import base64
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.user as user_service_module
from app.core.config import get_settings
from app.core.security import DUMMY_HASH
from app.models import User
from tests.conftest import MakeUser, basic_header

ME = "/api/v1/users/me"
ITEMS = "/api/v1/items"


# --- helpers ------------------------------------------------------------------


def _raw(payload: bytes) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(payload).decode()}


def _body(r: Response) -> dict[str, Any]:
    body: dict[str, Any] = r.json()
    return {k: v for k, v in body.items() if k != "request_id"}


def _assert_no_basic_challenge(r: Response) -> None:
    assert "basic" not in r.headers.get("www-authenticate", "").lower()


def _assert_invalid(r: Response) -> None:
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "invalid_credentials"
    _assert_no_basic_challenge(r)


@pytest.fixture
def prod(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the settings as in production; the cached settings are rebuilt around it."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("COOKIE_SECURE", "true")  # required outside dev when cookies exist
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- accepting credentials ----------------------------------------------------------


async def test_basic_valid_ok(anon_client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("basic@example.com")
    r = await anon_client.get(ME, headers=basic_header("basic@example.com"))
    assert r.status_code == 200
    assert r.json()["email"] == "basic@example.com"


async def test_basic_email_is_normalised_like_login(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("case@example.com")
    r = await anon_client.get(ME, headers=basic_header("  Case@Example.COM "))
    assert r.status_code == 200


async def test_password_may_contain_colons(anon_client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("colons@example.com", "pass:with:many:colons")
    headers = basic_header("colons@example.com", "pass:with:many:colons")
    ok = await anon_client.get(ME, headers=headers)
    assert ok.status_code == 200
    # Split on the first colon only: a prefix of the password is not the password.
    _assert_invalid(
        await anon_client.get(ME, headers=basic_header("colons@example.com", "pass"))
    )


async def test_basic_needs_no_csrf(anon_client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("nocsrf@example.com")
    r = await anon_client.post(
        ITEMS, json={"name": "via basic"}, headers=basic_header("nocsrf@example.com")
    )
    assert r.status_code == 201


async def test_basic_verifies_through_the_login_service(
    anon_client: AsyncClient, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_user("known@example.com")
    verified: list[str] = []
    real = user_service_module.verify_password

    def spy(password: str, hashed: str) -> bool:
        verified.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(user_service_module, "verify_password", spy)
    _assert_invalid(await anon_client.get(ME, headers=basic_header("nobody@example.com")))
    # An unknown account costs one hash verification, against the dummy hash.
    assert verified == [DUMMY_HASH]


# --- rejecting credentials ------------------------------------------------------------


async def test_basic_wrong_password_401_without_www_authenticate_basic(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("wrong@example.com")
    wrong = await anon_client.get(ME, headers=basic_header("wrong@example.com", "not-the-password"))
    unknown = await anon_client.get(ME, headers=basic_header("nobody@example.com"))
    _assert_invalid(wrong)
    _assert_invalid(unknown)
    assert "www-authenticate" not in wrong.headers
    assert "www-authenticate" not in unknown.headers
    # Unknown user and wrong password are indistinguishable.
    assert _body(wrong) == _body(unknown)


async def test_basic_inactive_user_401(anon_client: AsyncClient, make_user: MakeUser) -> None:
    await make_user("inactive@example.com", active=False)
    inactive = await anon_client.get(ME, headers=basic_header("inactive@example.com"))
    _assert_invalid(inactive)
    unknown = await anon_client.get(ME, headers=basic_header("nobody@example.com"))
    assert _body(inactive) == _body(unknown)


async def test_user_deactivated_after_the_fact_is_rejected(
    anon_client: AsyncClient, make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user("later@example.com")
    assert (await anon_client.get(ME, headers=basic_header(user.email))).status_code == 200
    async with maker() as s:
        await s.execute(update(User).where(User.id == user.id).values(is_active=False))
        await s.commit()
    _assert_invalid(await anon_client.get(ME, headers=basic_header(user.email)))


@pytest.mark.parametrize(
    "authorization",
    [
        "Basic !!!not-base64!!!",
        "Basic ab",  # bad padding
        "Basic",  # scheme without credentials
        "Basic ",
        "Basic " + base64.b64encode(b"no-colon-here").decode(),
        "Basic " + base64.b64encode(b"").decode(),
        "Basic " + base64.b64encode(b"user@example.com:\xff\xfe\xfa").decode(),
        "Basic " + base64.b64encode(b"\xff\xfe:password").decode(),
        "Basic " + base64.b64encode(b"user@example.com:" + b"x" * 5000).decode(),
    ],
    ids=[
        "not-base64",
        "bad-padding",
        "no-credentials",
        "blank-credentials",
        "no-colon",
        "empty-payload",
        "non-utf8-password",
        "non-utf8-email",
        "oversized-password",
    ],
)
async def test_malformed_basic_is_401_without_a_basic_challenge(
    anon_client: AsyncClient, make_user: MakeUser, authorization: str
) -> None:
    await make_user("user@example.com")
    r = await anon_client.get(ME, headers={"Authorization": authorization})
    _assert_invalid(r)


async def test_basic_without_credentials_is_not_authenticated_without_challenge(
    anon_client: AsyncClient,
) -> None:
    r = await anon_client.get(ME)
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
    _assert_no_basic_challenge(r)
    for other in ("Digest abc", "Negotiate abc", "Basicish abc"):
        r = await anon_client.get(ME, headers={"Authorization": other})
        assert r.status_code == 401
        assert r.json()["code"] == "not_authenticated"


async def test_scheme_name_is_case_insensitive(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("scheme@example.com")
    header = basic_header("scheme@example.com")["Authorization"].replace("Basic", "bAsIc")
    assert (await anon_client.get(ME, headers={"Authorization": header})).status_code == 200


# --- HTTPS requirement ----------------------------------------------------------------


async def test_basic_over_http_rejected_in_prod(
    anon_client: AsyncClient, make_user: MakeUser, prod: None
) -> None:
    await make_user("prod@example.com")
    r = await anon_client.get(ME, headers=basic_header("prod@example.com"))
    assert r.status_code == 401
    assert r.json()["code"] == "basic_requires_https"
    _assert_no_basic_challenge(r)
    # Rejected whatever the credentials: nothing is verified over plain http.
    r = await anon_client.get(ME, headers=basic_header("prod@example.com", "wrong"))
    assert r.json()["code"] == "basic_requires_https"


async def test_basic_over_https_ok_in_prod(
    application: FastAPI, make_user: MakeUser, prod: None
) -> None:
    await make_user("tls@example.com")
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        r = await c.get(ME, headers=basic_header("tls@example.com"))
        assert r.status_code == 200
        _assert_invalid(await c.get(ME, headers=basic_header("tls@example.com", "wrong")))


async def test_basic_over_http_ok_in_dev(anon_client: AsyncClient, make_user: MakeUser) -> None:
    assert get_settings().environment == "dev"
    await make_user("dev@example.com")
    r = await anon_client.get(ME, headers=basic_header("dev@example.com"))
    assert r.status_code == 200


{%- if bearer %}


async def test_basic_does_not_get_a_bearer_challenge(
    anon_client: AsyncClient, make_user: MakeUser
) -> None:
    await make_user("bearer-too@example.com")
    r = await anon_client.get(ME, headers=basic_header("bearer-too@example.com", "wrong-password"))
    _assert_invalid(r)
    assert "www-authenticate" not in r.headers  # Bearer's challenge is for Bearer failures
{%- endif %}

