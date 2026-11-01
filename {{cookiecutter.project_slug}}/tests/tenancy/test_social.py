{%- set rbac = cookiecutter.use_rbac == "y" -%}
{%- set basic = cookiecutter.use_auth_basic == "y" -%}
"""Social login under multi-tenancy: the tenant named at login is carried in the signed
state; only that tenant can receive a NEW user, and only if it allows social signup."""
from collections.abc import Awaitable, Callable

import jwt
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth.oauth_providers import SocialProfile
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models import SocialAccount, Tenant, User
{%- if rbac %}
from app.services.rbac import RbacService
{%- endif %}
from app.services.social import SocialLoginService
from app.services.tenant import TenantService
from app.services.user import UserAuthentication
from tests.conftest import (
    PASSWORD,
    MakeUser,
    SocialLogin,
{%- if basic %}
    basic_header,
{%- endif %}
    social_profile,
)

pytestmark = pytest.mark.usefixtures("oauth_settings")

ME = "/api/v1/users/me"
MakeSocialTenant = Callable[..., Awaitable[Tenant]]


@pytest.fixture
def make_social_tenant(maker: async_sessionmaker[AsyncSession]) -> MakeSocialTenant:
    """A tenant (through the real TenantService) with social signup on unless told otherwise."""

    async def _make(slug: str, *, allow: bool = True, active: bool = True) -> Tenant:
        async with maker() as session:
            return await TenantService(session).create_tenant(
                slug.title(), slug, is_active=active, allow_social_signup=allow
            )

    return _make


async def _resolve(
    maker: async_sessionmaker[AsyncSession], profile: SocialProfile, slug: str | None
) -> User:
    async with maker() as session:
        return await SocialLoginService(session).resolve(profile, slug)


async def _error(
    maker: async_sessionmaker[AsyncSession], profile: SocialProfile, slug: str | None
) -> AppError:
    with pytest.raises(AppError) as info:
        await _resolve(maker, profile, slug)
    assert info.value.status_code == 403
    return info.value


async def _counts(maker: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with maker() as session:
        users = await session.scalar(select(func.count()).select_from(User)) or 0
        accounts = await session.scalar(select(func.count()).select_from(SocialAccount)) or 0
        return users, accounts


def _body(r: Response) -> tuple[int, str, str]:
    return r.status_code, r.json()["code"], r.json()["detail"]


# --- the tenant travels in the signed state ----------------------------------------


async def test_login_carries_the_named_tenant_in_the_signed_state(
    anon_client: AsyncClient,
) -> None:
    r = await anon_client.get("/api/v1/oauth/google/login", params={"tenant": "acme"})
    assert r.status_code == 302
    assert "acme" not in r.headers["location"]  # not sent to the provider
    cookie = r.cookies["oauth_state"]
    claims = jwt.decode(cookie, get_settings().secret_key, algorithms=["HS256"])
    assert claims["tenant"] == "acme"
    plain = await anon_client.get("/api/v1/oauth/google/login")
    cookie = plain.cookies["oauth_state"]
    claims = jwt.decode(cookie, get_settings().secret_key, algorithms=["HS256"])
    assert claims["tenant"] is None
    bad = await anon_client.get("/api/v1/oauth/google/login", params={"tenant": "Not A Slug"})
    assert bad.status_code == 422


# --- new users ---------------------------------------------------------------------


async def test_new_user_created_when_tenant_allows_signup(
    maker: async_sessionmaker[AsyncSession], make_social_tenant: MakeSocialTenant
) -> None:
    acme = await make_social_tenant("acme")
    user = await _resolve(maker, social_profile("New@Example.com"), "acme")
    assert (user.email, user.tenant_id) == ("new@example.com", acme.id)
    assert user.is_active and not user.is_superuser
    async with maker() as session:
        [account] = (await session.scalars(select(SocialAccount))).all()
        assert account.user_id == user.id
{%- if rbac %}
        # The member role of THAT tenant.
        roles = await RbacService(session, acme.id).role_names([user.id])
        assert roles[user.id] == ["member"]
{%- endif %}
        # No password works for the new account.
        for password in (PASSWORD, ""):
            assert await UserAuthentication(session).authenticate(user.email, password) is None


@pytest.mark.parametrize(
    ("slug", "allow", "active"),
    [
        ("acme", False, True),  # the tenant does not allow social signup
        ("acme", True, False),  # inactive tenant
        ("missing", True, True),  # no such tenant
        (None, True, True),  # no tenant named at login
    ],
)
async def test_signup_denied_when_tenant_disallows(
    maker: async_sessionmaker[AsyncSession],
    make_social_tenant: MakeSocialTenant,
    slug: str | None,
    allow: bool,
    active: bool,
) -> None:
    await make_social_tenant("acme", allow=allow, active=active)
    before = await _counts(maker)
    error = await _error(maker, social_profile("new@example.com"), slug)
    assert error.code == "social_signup_disabled"
    assert await _counts(maker) == before


# --- existing users ----------------------------------------------------------------


async def test_existing_user_signs_in_to_their_own_tenant(
    maker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
    make_social_tenant: MakeSocialTenant,
    tenant: Tenant,
) -> None:
    user = await make_user("member@example.com")
    resolved = await _resolve(maker, social_profile("member@example.com"), None)
    assert (resolved.id, resolved.tenant_id) == (user.id, tenant.id)
    # Naming their own tenant is fine too (by subject now: the account is linked).
    again = await _resolve(maker, social_profile("member@example.com"), tenant.slug)
    assert again.id == user.id


async def test_user_of_another_tenant_than_the_named_one_gets_the_generic_denial(
    maker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
    make_social_tenant: MakeSocialTenant,
) -> None:
    await make_social_tenant("acme")
    await make_user("member@example.com")  # lives in the primary test tenant
    other = await _error(maker, social_profile("member@example.com"), "acme")
    await make_user("inactive@example.com", active=False)
    inactive = await _error(maker, social_profile("inactive@example.com", subject="s2"), None)
    # Identical: a denial never reveals which tenant an account belongs to.
    assert (other.code, other.detail) == (inactive.code, inactive.detail)
    assert other.code == "social_login_denied"
    assert (await _counts(maker))[1] == 0  # nothing linked
    # Once linked, the provider account cannot be used to enter another tenant either.
    await _resolve(maker, social_profile("member@example.com"), None)
    linked = await _error(maker, social_profile("member@example.com"), "acme")
    assert linked.code == "social_login_denied"


async def test_user_of_an_inactive_tenant_is_denied(
    maker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
    make_social_tenant: MakeSocialTenant,
) -> None:
    closed = await make_social_tenant("closed", active=False)
    await make_user("closed@example.com", tenant=closed)
    error = await _error(maker, social_profile("closed@example.com"), None)
    assert error.code == "social_login_denied"


# --- over HTTP ---------------------------------------------------------------------


async def test_http_signup_into_the_named_tenant_and_denials_look_alike(
    anon_client: AsyncClient,
    make_user: MakeUser,
    make_social_tenant: MakeSocialTenant,
    social_login: SocialLogin,
    maker: async_sessionmaker[AsyncSession],
) -> None:
    acme = await make_social_tenant("acme")
    r = await social_login(anon_client, social_profile("fresh@example.com"), tenant="acme")
    assert r.status_code in (200, 302), r.text
    async with maker() as session:
        stmt = select(User).where(User.email == "fresh@example.com")
        fresh = (await session.scalars(stmt)).one()
    assert fresh.tenant_id == acme.id
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

    await make_user("member@example.com")
    await make_user("inactive@example.com", active=False)
    other = await social_login(
        anon_client, social_profile("member@example.com", subject="m"), tenant="acme"
    )
    inactive = await social_login(anon_client, social_profile("inactive@example.com", subject="i"))
    assert _body(other) == _body(inactive)
    assert _body(other)[:2] == (403, "social_login_denied")
