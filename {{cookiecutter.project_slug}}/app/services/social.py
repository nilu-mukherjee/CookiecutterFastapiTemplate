{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
"""Social login: find, link or (only if allowed) create the user behind a provider account."""
import secrets
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.oauth_providers import SocialProfile
{%- if not tenancy %}
from app.core.config import get_settings
{%- endif %}
from app.core.exceptions import AppError
from app.models.social_account import SocialAccount
{%- if tenancy %}
from app.models.tenant import Tenant
{%- endif %}
from app.models.user import User
from app.repositories.social_account import SocialAccountRepository
{%- if tenancy %}
from app.repositories.tenant import TenantRepository
from app.repositories.user import GlobalUserLookup
{%- endif %}
from app.services.user import UserService


def _denied() -> AppError:
{%- if tenancy %}
    # One answer for an inactive user, an inactive tenant, a superuser and a user of another
    # tenant than the one the login named: never reveal which (or where an account exists).
{%- else %}
    # One answer for an inactive user, a superuser and an account already linked elsewhere.
{%- endif %}
    return AppError(403, "social_login_denied", "Social login is not available for this account")


def _signup_disabled() -> AppError:
    return AppError(403, "social_signup_disabled", "Sign-up through social login is disabled")


def _unusable_password() -> str:
    """A random secret that is never stored or shown: the new account has no usable password
    (until an admin sets one), while a password login against it still costs a normal
    argon2 verification, so it is indistinguishable from a wrong password."""
    return secrets.token_urlsafe(48)


class SocialLoginService:
{%- if tenancy %}
    """Resolves a provider account to a user. Like password login, this is the
    authentication stage: no tenant is known yet (it follows from the user), so it uses
    the named global lookups (GlobalUserLookup, TenantRepository, SocialAccountRepository).
    """
{%- else %}
    """Resolves a provider account to a user (the authentication stage of social login)."""
{%- endif %}

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.accounts = SocialAccountRepository(session)
{%- if tenancy %}
        self.lookup = GlobalUserLookup(session)
        self.tenants = TenantRepository(session)
{%- else %}
        self.users = UserService(session)
{%- endif %}

    async def resolve(self, profile: SocialProfile{% if tenancy %}, tenant_slug: str | None{% endif %}) -> User:
        """The user to sign in, or a 403 AppError.

        1. A linked account (provider, subject) signs its user in, whatever email the
           provider reports now.
        2. Otherwise the provider must have verified the email; an existing user with that
           email is linked and signed in.
{%- if tenancy %}
        3. Otherwise a new user is created only in the tenant the login named
           (``tenant_slug``), if that tenant is active and allows social signup.
{%- else %}
        3. Otherwise a new user is created only if SOCIAL_ALLOW_SIGNUP is true.
{%- endif %}
        """
{%- if tenancy %}
        named = await self.tenants.get_by_slug(tenant_slug) if tenant_slug else None
{%- endif %}
        provider = profile.provider.value
        account = await self.accounts.get_by_subject(provider, profile.subject)
        if account is not None:
            return await self._active(account.user_id{% if tenancy %}, tenant_slug, named{% endif %})
        if not profile.email_verified or not profile.email:
            raise AppError(
                403, "email_not_verified", "The provider has not verified this email address"
            )
        email = profile.email.strip().lower()
{%- if tenancy %}
        existing = await self.lookup.get_by_email_global(email)
        if existing is not None:
            user = await self._active(existing.id, tenant_slug, named)
            await self._link(user, profile, email)
            return user
        if named is None or not named.is_active or not named.allow_social_signup:
            raise _signup_disabled()
        return await self._sign_up(named, profile, email)
{%- else %}
        existing = await self.users.repo.get_by_email(email)
        if existing is not None:
            user = self._active_user(existing)
            await self._link(user, profile, email)
            return user
        if not get_settings().social_allow_signup:
            raise _signup_disabled()
        return await self._sign_up(profile, email)
{%- endif %}
{%- if tenancy %}

    async def _active(
        self, user_id: UUID, tenant_slug: str | None, named: Tenant | None
    ) -> User:
        # None unless both the user and their tenant are active.
        user = await self.lookup.get_active_for_authentication(user_id)
        # Platform superusers never sign in (or get linked) through a social account.
        if user is None or user.is_superuser:
            raise _denied()
        if tenant_slug is not None and (named is None or user.tenant_id != named.id):
            raise _denied()
        return user

    async def _sign_up(self, tenant: Tenant, profile: SocialProfile, email: str) -> User:
        """A new member of the tenant, linked to the provider account, in one transaction."""
        user = await UserService(self.session, tenant.id).stage(email, _unusable_password())
        await self._link(user, profile, email)
        return user
{%- else %}

    async def _active(self, user_id: UUID) -> User:
        return self._active_user(await self.users.repo.get(user_id))

    @staticmethod
    def _active_user(user: User | None) -> User:
        # Superusers never sign in (or get linked) through a social account.
        if user is None or not user.is_active or user.is_superuser:
            raise _denied()
        return user

    async def _sign_up(self, profile: SocialProfile, email: str) -> User:
        """A new user (default role{% if cookiecutter.use_rbac == "y" %}: member{% endif %}) linked to the provider account."""
        # Staged, not committed: the user and the link are one transaction (a lost link
        # race rolls the user back too).
        user = await self.users.stage(email, _unusable_password())
        await self._link(user, profile, email)
        return user
{%- endif %}

    async def _link(self, user: User, profile: SocialProfile, email: str) -> None:
        """Record the provider account for the user and commit."""
        provider = profile.provider.value
        if await self.accounts.get_for_user(user.id, provider) is not None:
            # The user already has another account at this provider: a second provider
            # account claiming the same email must not take the user over.
            raise _denied()
        try:
            await self.accounts.add(
                SocialAccount(
                    user_id=user.id, provider=provider, subject=profile.subject, email=email
                )
            )
            await self.session.commit()
        except IntegrityError as exc:  # lost a race on one of the unique constraints
            await self.session.rollback()
            raise _denied() from exc
