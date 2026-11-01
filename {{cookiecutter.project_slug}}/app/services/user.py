{%- set rbac = cookiecutter.use_rbac == "y" -%}
{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import (
    DUMMY_HASH,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_password,
)
{% if tenancy %}from app.models.tenant import SYSTEM_TENANT_SLUG
{% endif %}from app.models.user import User
{%- if tenancy %}
from app.repositories.tenant import TenantRepository
from app.repositories.user import GlobalUserLookup, UserRepository
{%- else %}
from app.repositories.user import UserRepository
{%- endif %}
{%- if rbac %}
from app.services.rbac import DEFAULT_ROLE, RbacService
{%- endif %}

logger = logging.getLogger("app.users")


{% if tenancy %}def _email_taken() -> AppError:
    # One answer whichever tenant holds the email: never reveal where it exists.
    return AppError(409, "email_taken", "A user with this email already exists")


class UserAuthentication:
    """The authentication stage: find the user behind a credential, before any tenant
    is known (the tenant is derived from the user). Unscoped by design; it only ever
    answers "which active user is this", never serves tenant data.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.lookup = GlobalUserLookup(session)

    async def authenticate(self, email: str, password: str) -> User | None:
        """Return the user for valid credentials of an active account in an active
        tenant, else None.

        A hash is verified on every path (a dummy one for unknown emails) so the
        response time does not reveal whether the account exists.
        """
        user = await self.lookup.get_by_email_global(email)
        if user is None:
            verify_password(password, DUMMY_HASH)
            return None
        if not verify_password(password, user.hashed_password) or not user.is_active:
            return None
        if not await self.lookup.tenant_is_active(user.tenant_id):
            return None
        return user

    async def get_active(self, user_id: UUID) -> User | None:
        """The user behind a token/session/key, if the user and the tenant are active."""
        return await self.lookup.get_active_for_authentication(user_id)


class UserService:
    """Users of ONE tenant: the caller's (``Principal.tenant_id``)."""

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.repo = UserRepository(session, tenant_id)
        self.lookup = GlobalUserLookup(session)
{%- if rbac %}
        self.rbac = RbacService(session, tenant_id)
{%- endif %}
{%- else %}class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = UserRepository(session)
{%- if rbac %}
        self.rbac = RbacService(session)
{%- endif %}
{%- endif %}

    async def create(
        self,
        email: str,
        password: str,
        *,
        is_superuser: bool = False,
        is_active: bool = True,
{%- if rbac %}
        roles: Sequence[str] | None = None,
{%- endif %}
    ) -> User:
{%- if tenancy %}
        user = await self.stage(
            email,
            password,
            is_superuser=is_superuser,
            is_active=is_active,
{%- if rbac %}
            roles=roles,
{%- endif %}
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:  # lost a race on the unique email index
            await self.session.rollback()
            raise _email_taken() from exc
        return user

    async def stage(
        self,
        email: str,
        password: str,
        *,
        is_superuser: bool = False,
        is_active: bool = True,
{%- if rbac %}
        roles: Sequence[str] | None = None,
{%- endif %}
    ) -> User:
        """Validate and insert the user in this tenant WITHOUT committing.

        Lets tenant creation add its first admin in the same transaction as the tenant.
        """
{%- endif %}
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise AppError(
                422,
                "invalid_password",
                f"Password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters",
            )
        email = email.strip().lower()
{%- if tenancy %}
        # Emails are unique across ALL tenants: checked globally, same answer everywhere.
        if await self.lookup.get_by_email_global(email) is not None:
            raise _email_taken()
        if is_superuser and not await self._in_system_tenant():
            # Superusers operate the platform; they only ever live in the system tenant.
            raise AppError(
                403, "forbidden", "Superusers can only be created in the system tenant"
            )
{%- else %}
        if await self.repo.get_by_email(email) is not None:
            raise AppError(409, "email_taken", "A user with this email already exists")
{%- endif %}
{%- if rbac %}
        # Before the insert: unknown roles must not leave a user behind, and re-seeding a
        # missing default role commits.
        role_objs = await self.rbac.resolve_roles([DEFAULT_ROLE] if roles is None else roles)
{%- endif %}
        user = User(
            email=email,
            hashed_password=hash_password(password),
            is_superuser=is_superuser,
            is_active=is_active,
        )
{%- if tenancy %}
        try:
            await self.repo.add(user)
        except IntegrityError as exc:  # lost a race on the unique email index
            await self.session.rollback()
            raise _email_taken() from exc
{%- if rbac %}
        await self.rbac.assign(user.id, role_objs)
{%- endif %}
        return user
{%- else %}
        try:
            await self.repo.add(user)
{%- if rbac %}
            await self.rbac.assign(user.id, role_objs)
{%- endif %}
            await self.session.commit()
        except IntegrityError as exc:  # lost a race on the unique email index
            await self.session.rollback()
            raise AppError(409, "email_taken", "A user with this email already exists") from exc
        return user
{%- endif %}

    async def get(self, user_id: UUID) -> User:
        user = await self.repo.get(user_id)
        if user is None:
            raise AppError(404, "not_found", "User not found")
        return user

    async def list(self, limit: int, offset: int) -> tuple[Sequence[User], int]:
        return await self.repo.list(limit, offset), await self.repo.count()
{%- if not tenancy %}

    async def authenticate(self, email: str, password: str) -> User | None:
        """Return the user for valid credentials of an active account, else None.

        A hash is verified on every path (a dummy one for unknown emails) so the
        response time does not reveal whether the account exists.
        """
        user = await self.repo.get_by_email(email)
        if user is None:
            verify_password(password, DUMMY_HASH)
            return None
        if not verify_password(password, user.hashed_password) or not user.is_active:
            return None
        return user
{%- endif %}

    async def bootstrap_superuser(self) -> User | None:
{%- if tenancy %}
        """Create the first superuser from settings if configured and absent.

        Call it on a service for the system tenant: superusers live there.
        """
{%- else %}
        """Create the first superuser from settings if configured and absent."""
{%- endif %}
        settings = get_settings()
        email, password = settings.first_superuser_email, settings.first_superuser_password
        if not email or not password:
            return None
        if await {% if tenancy %}self.lookup.get_by_email_global{% else %}self.repo.get_by_email{% endif %}(email) is not None:
            return None
        try:
            return await self.create(
                email,
                password,
                is_superuser=True,
{%- if rbac %}
                roles=["admin"],
{%- endif %}
            )
        except AppError as exc:
            if exc.code != "email_taken":
                raise
            # Another worker created it between our check and insert (concurrent startup).
            await self.session.rollback()
            logger.info("first superuser already created by another process")
            return None
{%- if tenancy %}

    async def _in_system_tenant(self) -> bool:
        system = await TenantRepository(self.session).get_by_slug(SYSTEM_TENANT_SLUG)
        return system is not None and system.id == self.tenant_id
{%- endif %}
