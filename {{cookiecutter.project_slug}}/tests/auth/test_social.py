{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
{%- set rbac = cookiecutter.use_rbac == "y" -%}
{%- set basic = cookiecutter.use_auth_basic == "y" -%}
{#- Social login issues the first enabled of: session, JWT cookie, JWT Bearer. -#}
{%- set method = "session" if session else ("jwt_cookie" if jwt_cookie else "bearer") -%}
"""Social login: the OAuth2 authorization-code + PKCE flow, account resolution and the
credential issued on success."""
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from httpx import AsyncClient, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth.oauth_providers import ProviderError, ProviderName, SocialProfile
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.models import SocialAccount, User
{%- if rbac and not tenancy %}
from app.services.rbac import RbacService
{%- endif %}
from app.services.social import SocialLoginService
{%- if not tenancy %}
from app.services.user import UserService
{%- endif %}
from tests.conftest import (
    GOOGLE_SECRET,
{%- if not tenancy %}
    PASSWORD,
{%- endif %}
    REDIRECT_BASE,
    FakeProviders,
{%- if session %}
    MakeSessionClient,
{%- endif %}
    MakeUser,
    SocialLogin,
{%- if basic and not tenancy %}
    basic_header,
{%- endif %}
    social_profile,
)

pytestmark = pytest.mark.usefixtures("oauth_settings")

OAUTH = "/api/v1/oauth"
STATE_COOKIE = "oauth_state"
ME = "/api/v1/users/me"


# --- helpers ------------------------------------------------------------------------


def _set_cookies(r: Response) -> dict[str, tuple[str, dict[str, str]]]:
    """name -> (value, lowercased attributes) of every Set-Cookie header."""
    out = {}
    for raw in r.headers.get_list("set-cookie"):
        first, *rest = [part.strip() for part in raw.split(";")]
        name, _, value = first.partition("=")
        attrs = {}
        for attr in rest:
            key, _, val = attr.partition("=")
            attrs[key.lower()] = val
        out[name] = (value.strip('"'), attrs)
    return out


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _cleared(r: Response) -> bool:
    """The response deletes the state cookie (same Path, so the browser drops it)."""
    cookie = _set_cookies(r).get(STATE_COOKIE)
    return cookie is not None and cookie[1].get("max-age") == "0" and cookie[1]["path"] == OAUTH


async def _start(c: AsyncClient, provider: str = "google") -> tuple[str, str]:
    """Begin a login; return (state sent to the provider, the state cookie)."""
    r = await c.get(f"{OAUTH}/{provider}/login")
    assert r.status_code == 302
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return state, _set_cookies(r)[STATE_COOKIE][0]


def _forge(state: str, *, key: str | None = None, **overrides: Any) -> str:
    """A state cookie with chosen claims, signed with the real key unless given another."""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "typ": "oauth_state",
        "nonce": state,
        "code_verifier": "v" * 64,
        "provider": "google",
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    claims.update(overrides)
    return jwt.encode(claims, key or get_settings().secret_key, algorithm="HS256")


async def _callback(
    c: AsyncClient, state: str, *, cookie: str | None = None, provider: str = "google",
    **params: str,
) -> Response:
    """Hit the callback; ``cookie`` replaces whatever state cookie the client holds."""
    headers = {}
    if cookie is not None:
        c.cookies.clear()
        headers["Cookie"] = f"{STATE_COOKIE}={cookie}"
    query = {"code": "the-code", "state": state, **params}
    return await c.get(f"{OAUTH}/{provider}/callback", params=query, headers=headers)


def _assert_rejected(r: Response, code: str = "invalid_oauth_state", status: int = 400) -> None:
    assert r.status_code == status, r.text
    assert r.json()["code"] == code
    # Single use: whatever went wrong, the state cookie is gone.
    assert _cleared(r)


def _state_claims(r: Response) -> dict[str, object]:
    value, _ = _set_cookies(r)[STATE_COOKIE]
    claims: dict[str, object] = jwt.decode(
        value, get_settings().secret_key, algorithms=["HS256"]
    )
    return claims


# --- settings -----------------------------------------------------------------------


def test_social_env_example_keys_are_settings_fields() -> None:
    env = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    keys = [line.split("=", 1)[0] for line in env.splitlines() if line and line[0] != "#"]
    prefixes = ("GOOGLE_", "GITHUB_", "OAUTH_", "SOCIAL_", "COOKIE_")
    social = [k for k in keys if k.startswith(prefixes)]
    assert {"GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_SECRET", "OAUTH_SUCCESS_REDIRECT_URL"} <= set(
        social
    )
    for key in social:
        assert key.lower() in Settings.model_fields, key


def test_client_secrets_never_appear_in_repr_or_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    assert settings.google_client_secret is not None
    assert settings.google_client_secret.get_secret_value() == GOOGLE_SECRET
    assert GOOGLE_SECRET not in repr(settings) and GOOGLE_SECRET not in str(settings)
    monkeypatch.setenv("ENVIRONMENT", "prod")  # with COOKIE_SECURE=false: invalid
    with pytest.raises(ValidationError) as info:
        Settings()  # type: ignore[call-arg]
    assert GOOGLE_SECRET not in str(info.value)


def test_state_cookie_must_be_secure_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    # Applies to every social build, Bearer-only ones included (the state cookie).
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(environment="prod", secret_key="x" * 40, cookie_secure=False)
    assert Settings(environment="prod", secret_key="x" * 40, cookie_secure=True).cookie_secure


async def test_empty_credentials_disable_the_provider(
    anon_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", "")  # as left in .env.example
    get_settings.cache_clear()
    assert get_settings().github_client_id is None
    assert (await anon_client.get(f"{OAUTH}/github/login")).status_code == 404
    assert (await anon_client.get(f"{OAUTH}/google/login")).status_code == 302


# --- login: redirect to the provider -----------------------------------------------


@pytest.mark.parametrize(
    ("provider", "host", "path", "scope"),
    [
        ("google", "accounts.google.com", "/o/oauth2/v2/auth", "openid email profile"),
        ("github", "github.com", "/login/oauth/authorize", "read:user user:email"),
    ],
)
async def test_login_redirect_has_pkce_and_state(
    anon_client: AsyncClient, provider: str, host: str, path: str, scope: str
) -> None:
    r = await anon_client.get(f"{OAUTH}/{provider}/login")
    assert r.status_code == 302
    location = r.headers["location"]
    url = urlsplit(location)
    assert (url.scheme, url.netloc, url.path) == ("https", host, path)
    query = parse_qs(url.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [f"{provider}-client-id"]
    # Fixed from settings, never derived from the request's Host header.
    assert query["redirect_uri"] == [f"{REDIRECT_BASE}{OAUTH}/{provider}/callback"]
    assert query["scope"] == [scope]
    assert query["code_challenge_method"] == ["S256"]

    claims = _state_claims(r)
    assert claims["typ"] == "oauth_state"
    assert claims["provider"] == provider
    verifier = claims["code_verifier"]
    assert isinstance(verifier, str) and 43 <= len(verifier) <= 128
    assert query["state"] == [claims["nonce"]]
    assert query["code_challenge"] == [_s256(verifier)]
    # The verifier only ever lives in the HttpOnly cookie, never in the URL.
    assert verifier not in location
    assert set(query) == {
        "response_type", "client_id", "redirect_uri", "scope", "state",
        "code_challenge", "code_challenge_method",
    }


async def test_login_sets_a_short_lived_httponly_lax_state_cookie(
    anon_client: AsyncClient,
) -> None:
    r = await anon_client.get(f"{OAUTH}/google/login")
    _, attrs = _set_cookies(r)[STATE_COOKIE]
    assert "httponly" in attrs
    assert attrs["path"] == OAUTH
    assert attrs["samesite"].lower() == "lax"
    assert attrs["max-age"] == "600"
    assert "secure" not in attrs  # COOKIE_SECURE=false in dev
    assert "domain" not in attrs
    claims = _state_claims(r)
    assert isinstance(claims["exp"], int) and isinstance(claims["iat"], int)
    assert claims["exp"] - claims["iat"] == 600


async def test_state_cookie_is_lax_and_secure_per_settings(
    anon_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The callback is a cross-site top-level GET: a Strict cookie would not be sent.
    monkeypatch.setenv("COOKIE_SAMESITE", "strict")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("COOKIE_DOMAIN", "example.com")
    get_settings.cache_clear()
    r = await anon_client.get(f"{OAUTH}/github/login")
    _, attrs = _set_cookies(r)[STATE_COOKIE]
    assert attrs["samesite"].lower() == "lax"
    assert "secure" in attrs
    assert attrs["domain"] == "example.com"


async def test_each_login_gets_a_fresh_state_and_verifier(anon_client: AsyncClient) -> None:
    first = _state_claims(await anon_client.get(f"{OAUTH}/google/login"))
    second = _state_claims(await anon_client.get(f"{OAUTH}/google/login"))
    assert first["nonce"] != second["nonce"]
    assert first["code_verifier"] != second["code_verifier"]


@pytest.mark.parametrize("route", ["login", "callback?code=c&state=s"])
async def test_unknown_and_unconfigured_providers_are_404(
    anon_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    unknown = await anon_client.get(f"{OAUTH}/facebook/{route}")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET")
    get_settings.cache_clear()
    unconfigured = await anon_client.get(f"{OAUTH}/google/{route}")
    assert unknown.status_code == unconfigured.status_code == 404
    # Indistinguishable: a disabled provider looks exactly like one that does not exist.
    assert unknown.json()["code"] == unconfigured.json()["code"]
    assert unknown.json()["detail"] == unconfigured.json()["detail"]
    # The configured provider still works.
    assert (await anon_client.get(f"{OAUTH}/github/login")).status_code == 302



# --- callback: the state check -----------------------------------------------------


async def test_callback_without_state_cookie_is_400(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    state, _ = await _start(anon_client)
    anon_client.cookies.clear()
    _assert_rejected(await _callback(anon_client, state))
    assert fake_providers[ProviderName.GOOGLE].calls == []


async def test_callback_with_tampered_state_cookie_is_400(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    state, cookie = await _start(anon_client)
    header, payload, signature = cookie.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    _assert_rejected(await _callback(anon_client, state, cookie=f"{header}.{payload}.{flipped}"))
    # Re-signed with another key: just as invalid.
    _assert_rejected(await _callback(anon_client, state, cookie=_forge(state, key="k" * 40)))
    assert fake_providers[ProviderName.GOOGLE].calls == []


async def test_callback_with_expired_state_cookie_is_400(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    past = datetime.now(UTC) - timedelta(minutes=11)
    cookie = _forge("s", iat=past, exp=past + timedelta(minutes=10))
    _assert_rejected(await _callback(anon_client, "s", cookie=cookie))
    # The same cookie, unexpired, is accepted as far as the state check goes.
    fake_providers[ProviderName.GOOGLE].error = ProviderError("stop here")
    r = await _callback(anon_client, "s", cookie=_forge("s"))
    assert r.status_code == 502


async def test_callback_with_another_providers_state_is_400(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    state, _ = await _start(anon_client, "google")
    _assert_rejected(await _callback(anon_client, state, provider="github"))
    assert fake_providers[ProviderName.GITHUB].calls == []


async def test_callback_with_mismatched_state_is_400(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    state, _ = await _start(anon_client)
    _assert_rejected(await _callback(anon_client, state + "x"))
    state, _ = await _start(anon_client)
    _assert_rejected(await _callback(anon_client, ""))
    assert fake_providers[ProviderName.GOOGLE].calls == []


async def test_alg_none_state_cookie_is_rejected(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    def b64(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    now = int(datetime.now(UTC).timestamp())
    claims = {"typ": "oauth_state", "nonce": "s", "code_verifier": "v" * 64,
              "provider": "google", "iat": now, "exp": now + 600}
    forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."
    _assert_rejected(await _callback(anon_client, "s", cookie=forged))
    assert fake_providers[ProviderName.GOOGLE].calls == []


async def test_a_token_with_another_purpose_is_not_a_state(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    # Correctly signed with the same key, but not minted as an OAuth state.
    _assert_rejected(await _callback(anon_client, "s", cookie=_forge("s", typ="access")))
    assert fake_providers[ProviderName.GOOGLE].calls == []


async def test_provider_error_param_is_400_and_nothing_is_fetched(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    state, _ = await _start(anon_client)
    r = await _callback(anon_client, state, error="access_denied")
    _assert_rejected(r, "oauth_denied")
    assert "access_denied" not in r.text  # generic
    assert fake_providers[ProviderName.GOOGLE].calls == []


async def test_callback_without_code_is_400(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    state, _ = await _start(anon_client)
    r = await anon_client.get(f"{OAUTH}/google/callback", params={"state": state})
    _assert_rejected(r, "oauth_denied")
    assert fake_providers[ProviderName.GOOGLE].calls == []


async def test_provider_failure_is_a_generic_502(
    anon_client: AsyncClient, fake_providers: FakeProviders
) -> None:
    fake = fake_providers[ProviderName.GOOGLE]
    fake.error = ProviderError("google: token exchange failed")
    state, cookie = await _start(anon_client)
    r = await _callback(anon_client, state)
    _assert_rejected(r, "oauth_provider_error", 502)
    # The provider got the verifier from the cookie and the registered redirect URI.
    verifier = jwt.decode(cookie, get_settings().secret_key, algorithms=["HS256"])
    assert fake.calls == [
        ("the-code", verifier["code_verifier"], f"{REDIRECT_BASE}{OAUTH}/google/callback")
    ]
    for secret in (GOOGLE_SECRET, "the-code", verifier["code_verifier"], "token exchange"):
        assert secret not in r.text


# --- account resolution (SocialLoginService) ---------------------------------------


async def _resolve(maker: async_sessionmaker[AsyncSession], profile: SocialProfile) -> User:
    async with maker() as session:
        return await SocialLoginService(session).resolve(profile{% if tenancy %}, None{% endif %})


async def _resolve_error(
    maker: async_sessionmaker[AsyncSession], profile: SocialProfile
) -> AppError:
    with pytest.raises(AppError) as info:
        await _resolve(maker, profile)
    assert info.value.status_code == 403
    return info.value


async def _accounts(maker: async_sessionmaker[AsyncSession]) -> list[SocialAccount]:
    async with maker() as session:
        return list((await session.scalars(select(SocialAccount))).all())


async def _user_count(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as session:
        return await session.scalar(select(func.count()).select_from(User)) or 0


async def test_existing_verified_email_links_account(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    user = await make_user("social@example.com")
    resolved = await _resolve(maker, social_profile("  Social@Example.COM "))
    assert resolved.id == user.id
    [account] = await _accounts(maker)
    assert (account.user_id, account.provider, account.subject, account.email) == (
        user.id, "google", "subject-1", "social@example.com"
    )


async def test_second_login_resolves_by_provider_subject_even_if_the_email_changed(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    user = await make_user("social@example.com")
    await _resolve(maker, social_profile("social@example.com"))
    await make_user("someone-else@example.com")
    # Same provider account, now reporting another (even unverified) address.
    moved = social_profile("someone-else@example.com", verified=False)
    assert (await _resolve(maker, moved)).id == user.id
    [account] = await _accounts(maker)
    assert account.email == "social@example.com"  # as at link time


async def test_one_user_can_link_google_and_github(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    user = await make_user("social@example.com")
    await _resolve(maker, social_profile(provider=ProviderName.GOOGLE, subject="g"))
    await _resolve(maker, social_profile(provider=ProviderName.GITHUB, subject="1"))
    assert {(a.provider, a.user_id) for a in await _accounts(maker)} == {
        ("google", user.id), ("github", user.id)
    }


@pytest.mark.parametrize("email", ["social@example.com", None])
async def test_unverified_provider_email_rejected(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser, email: str | None
) -> None:
    await make_user("social@example.com")
    error = await _resolve_error(maker, social_profile(email, verified=False))
    assert error.code == "email_not_verified"
    assert await _accounts(maker) == []


async def test_github_primary_unverified_email_does_not_link(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    # What GitHubProvider reports when the primary address is unverified.
    await make_user("main@example.com")
    profile = social_profile("main@example.com", provider=ProviderName.GITHUB, verified=False)
    assert (await _resolve_error(maker, profile)).code == "email_not_verified"
    assert await _accounts(maker) == []


async def test_inactive_user_is_denied_by_email_and_by_subject(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    await make_user("inactive@example.com", active=False)
    error = await _resolve_error(maker, social_profile("inactive@example.com"))
    assert error.code == "social_login_denied"
    assert await _accounts(maker) == []

    linked = await make_user("linked@example.com")
    await _resolve(maker, social_profile("linked@example.com", subject="s2"))
    async with maker() as session:
        row = await session.get(User, linked.id)
        assert row is not None
        row.is_active = False
        await session.commit()
    error = await _resolve_error(maker, social_profile("linked@example.com", subject="s2"))
    assert error.code == "social_login_denied"


async def test_a_second_account_of_the_same_provider_cannot_take_over_a_user(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    await make_user("social@example.com")
    await _resolve(maker, social_profile(subject="first"))
    error = await _resolve_error(maker, social_profile(subject="second"))
    assert error.code == "social_login_denied"
    assert [a.subject for a in await _accounts(maker)] == ["first"]


async def test_a_provider_account_belongs_to_one_user_and_a_user_has_one_per_provider(
    maker: async_sessionmaker[AsyncSession], make_user: MakeUser
) -> None:
    alice = await make_user("alice@example.com")
    bob = await make_user("bob@example.com")
    async with maker() as session:
        session.add(SocialAccount(user_id=alice.id, provider="google", subject="s", email="a"))
        await session.commit()
    async with maker() as session:
        session.add(SocialAccount(user_id=bob.id, provider="google", subject="s", email="b"))
        with pytest.raises(IntegrityError):
            await session.commit()
    async with maker() as session:
        session.add(SocialAccount(user_id=alice.id, provider="google", subject="t", email="a"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_signup_denied_by_default(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    before = await _user_count(maker)
    error = await _resolve_error(maker, social_profile("new@example.com"))
    assert error.code == "social_signup_disabled"
    assert await _user_count(maker) == before
    assert await _accounts(maker) == []
{%- if not tenancy %}


async def test_new_user_created_when_signup_is_enabled(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOCIAL_ALLOW_SIGNUP", "true")
    get_settings.cache_clear()
    user = await _resolve(maker, social_profile("New@Example.com"))
    assert user.email == "new@example.com"
    assert user.is_active and not user.is_superuser
    [account] = await _accounts(maker)
    assert account.user_id == user.id
    async with maker() as session:
        # No password works: the stored hash is of a random secret nobody knows.
        for password in (PASSWORD, "", user.hashed_password):
            assert await UserService(session).authenticate(user.email, password) is None
{%- if rbac %}
        assert (await RbacService(session).role_names([user.id]))[user.id] == ["member"]
{%- endif %}
{%- endif %}


# --- the credential issued on success ({{ method }}) --------------------------------
{%- if method == "session" %}


async def test_success_starts_a_session_and_redirects(
    anon_client: AsyncClient, make_user: MakeUser, social_login: SocialLogin
) -> None:
    user = await make_user("social@example.com")
    r = await social_login(anon_client, social_profile("social@example.com"))
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/"
    cookies = _set_cookies(r)
    _, session_attrs = cookies["session_id"]
    assert "httponly" in session_attrs and session_attrs["path"] == "/"
    csrf_token, csrf_attrs = cookies["csrf_token"]
    assert "httponly" not in csrf_attrs
    assert _cleared(r)
    # The session works against the real authenticators (GET, then an unsafe request
    # that needs the CSRF token).
    me = await anon_client.get(ME)
    assert me.status_code == 200, me.text
    assert me.json()["id"] == str(user.id)
    logout = await anon_client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token}
    )
    assert logout.status_code == 204
    assert (await anon_client.get(ME)).status_code == 401


async def test_social_login_never_adopts_a_presented_session_id(
    make_user: MakeUser, make_session_client: MakeSessionClient, social_login: SocialLogin
) -> None:
    await make_user("social@example.com")
    c = await make_session_client("social@example.com")
    old = c.cookies["session_id"]
    r = await social_login(c, social_profile("social@example.com"))
    assert r.status_code == 302
    new = _set_cookies(r)["session_id"][0]
    assert new != old
    # The session that came with the request is gone (session fixation).
    c.cookies.clear()
    stale = await c.get(ME, headers={"Cookie": f"session_id={old}"})
    assert stale.status_code == 401
{%- elif method == "jwt_cookie" %}


async def test_success_sets_jwt_cookies_and_redirects(
    anon_client: AsyncClient, make_user: MakeUser, social_login: SocialLogin
) -> None:
    user = await make_user("social@example.com")
    r = await social_login(anon_client, social_profile("social@example.com"))
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/"
    cookies = _set_cookies(r)
    assert "httponly" in cookies["access_token"][1]
    assert cookies["refresh_token"][1]["path"] == "/api/v1/auth"
    csrf_token, csrf_attrs = cookies["csrf_token"]
    assert "httponly" not in csrf_attrs
    assert _cleared(r)
    me = await anon_client.get(ME)
    assert me.status_code == 200, me.text
    assert me.json()["id"] == str(user.id)
    # The refresh cookie works too (an unsafe request: CSRF token required).
    refreshed = await anon_client.post(
        "/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf_token}
    )
    assert refreshed.status_code == 200, refreshed.text
{%- else %}


async def test_success_returns_a_bearer_token_pair_not_cached(
    anon_client: AsyncClient, make_user: MakeUser, social_login: SocialLogin
) -> None:
    user = await make_user("social@example.com")
    r = await social_login(anon_client, social_profile("social@example.com"))
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert "location" not in r.headers
    body = r.json()
    assert set(body) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer"
    # Only the state cookie is touched (cleared); no credential cookies.
    assert set(_set_cookies(r)) == {STATE_COOKIE}
    assert _cleared(r)
    anon_client.cookies.clear()
    me = await anon_client.get(ME, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["id"] == str(user.id)
    refreshed = await anon_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
{%- endif %}


async def test_success_redirect_ignores_redirect_parameters(
    anon_client: AsyncClient,
    make_user: MakeUser,
    social_login: SocialLogin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_user("social@example.com")
    evil = "https://evil.example/steal"
    extra = {"next": evil, "redirect": evil, "redirect_uri": evil, "return_to": evil}
    r = await social_login(anon_client, social_profile("social@example.com"), extra=extra)
{%- if method == "bearer" %}
    assert r.status_code == 200
    assert "location" not in r.headers
    assert evil not in r.text
{%- else %}
    assert r.headers["location"] == "/"
    # The target comes from settings only.
    monkeypatch.setenv("OAUTH_SUCCESS_REDIRECT_URL", "/app/home")
    get_settings.cache_clear()
    r = await social_login(anon_client, social_profile("social@example.com"), extra=extra)
    assert r.headers["location"] == "/app/home"
{%- endif %}


async def test_the_state_is_single_use(
    anon_client: AsyncClient, make_user: MakeUser, fake_providers: FakeProviders
) -> None:
    await make_user("social@example.com")
    fake_providers[ProviderName.GOOGLE].profile = social_profile("social@example.com")
    state, _ = await _start(anon_client)
    first = await _callback(anon_client, state)
    assert first.status_code in (200, 302)
    assert _cleared(first)
    _assert_rejected(await _callback(anon_client, state))


async def test_rejections_over_http_clear_the_state(
    anon_client: AsyncClient, make_user: MakeUser, social_login: SocialLogin
) -> None:
    r = await social_login(anon_client, social_profile("nobody@example.com"))
    _assert_rejected(r, "social_signup_disabled", 403)
    r = await social_login(anon_client, social_profile("nobody@example.com", verified=False))
    _assert_rejected(r, "email_not_verified", 403)
    await make_user("inactive@example.com", active=False)
    r = await social_login(anon_client, social_profile("inactive@example.com"))
    _assert_rejected(r, "social_login_denied", 403)
    assert not any(name in _set_cookies(r) for name in ("session_id", "access_token"))
{%- if not tenancy %}


async def test_a_social_signup_has_no_usable_password(
    anon_client: AsyncClient, social_login: SocialLogin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOCIAL_ALLOW_SIGNUP", "true")
    get_settings.cache_clear()
    r = await social_login(anon_client, social_profile("fresh@example.com"))
    assert r.status_code in (200, 302), r.text
    anon_client.cookies.clear()
    for password in (PASSWORD, "x" * 64):
        login = await anon_client.post(
            "/api/v1/auth/login", json={"email": "fresh@example.com", "password": password}
        )
        assert login.status_code == 401
{%- if basic %}
        basic = await anon_client.get(ME, headers=basic_header("fresh@example.com", password))
        assert basic.status_code == 401
{%- endif %}
{%- endif %}


async def test_the_callback_is_rate_limited_like_everything_else(
    anon_client: AsyncClient,
) -> None:
    for _ in range(120):
        r = await anon_client.get(f"{OAUTH}/google/callback", params={"code": "c", "state": "s"})
        assert r.status_code == 400
    r = await anon_client.get(f"{OAUTH}/google/callback", params={"code": "c", "state": "s"})
    assert r.status_code == 429
