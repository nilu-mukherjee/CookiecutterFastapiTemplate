{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
{% if tenancy %}from uuid import UUID

{% endif %}from sqlalchemy import select
{% if tenancy %}from sqlalchemy.ext.asyncio import AsyncSession
{% endif %}
{% if tenancy %}from app.models.tenant import Tenant
{% endif %}from app.models.user import User
{% if tenancy %}from app.repositories.base import TenantScopedRepository


class UserRepository(TenantScopedRepository[User]):
    """Users of ONE tenant (see TenantScopedRepository)."""

    model = User

    async def get_by_email(self, email: str) -> User | None:
        """Emails are stored lowercased; the lookup lowercases too."""
        stmt = self._scoped(select(User)).where(User.email == email.strip().lower())
        return (await self.session.scalars(stmt)).one_or_none()


class GlobalUserLookup:
    """UNSCOPED reads of users, deliberately outside any tenant.

    Only for the authentication stage, where no tenant is known yet (the request's
    tenant is *derived* from the user found here), and for the global email uniqueness
    check (login is by email alone). Never use it to serve data to a request: use the
    tenant-scoped UserRepository for that.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email_global(self, email: str) -> User | None:
        """The user with this email in any tenant, active or not."""
        stmt = select(User).where(User.email == email.strip().lower())
        return (await self.session.scalars(stmt)).one_or_none()

    async def get_active_for_authentication(self, user_id: UUID) -> User | None:
        """The user, only if both the user and their tenant are active.

        Every authenticator calls this on every request, so deactivating a user or a
        tenant takes effect on the next request whatever the credential.
        """
        stmt = (
            select(User)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(User.id == user_id, User.is_active.is_(True), Tenant.is_active.is_(True))
        )
        return (await self.session.scalars(stmt)).one_or_none()

    async def tenant_is_active(self, tenant_id: UUID) -> bool:
        stmt = select(Tenant.is_active).where(Tenant.id == tenant_id)
        return bool(await self.session.scalar(stmt))
{%- else %}from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        """Emails are stored lowercased; the lookup lowercases too."""
        stmt = select(User).where(User.email == email.strip().lower())
        return (await self.session.scalars(stmt)).one_or_none()
{%- endif %}
