{%- set rbac = cookiecutter.use_rbac == "y" -%}
{%- set social = cookiecutter.use_social_login == "y" -%}
import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.tenant import SYSTEM_TENANT_SLUG, Tenant
from app.repositories.tenant import TenantRepository
{%- if rbac %}
from app.services.rbac import RbacService
{%- endif %}
from app.services.user import UserService

logger = logging.getLogger("app.tenants")


def _slug_taken() -> AppError:
    return AppError(409, "slug_taken", "A tenant with this slug already exists")


class TenantService:
    """Tenant management (superuser endpoints) and the system tenant at startup."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TenantRepository(session)

    async def ensure_system_tenant(self) -> Tenant:
        """Return the system tenant, creating it if absent.

        Idempotent and safe when several workers start at once: the loser of the race
        hits the unique slug index, rolls back and reads the winner's row.
        """
        existing = await self.repo.get_by_slug(SYSTEM_TENANT_SLUG)
        if existing is not None:
            return existing
        try:
            tenant = await self.repo.add(Tenant(name="System", slug=SYSTEM_TENANT_SLUG))
            await self.session.commit()
            return tenant
        except IntegrityError:
            await self.session.rollback()
            logger.info("system tenant already created by another process")
            found = await self.repo.get_by_slug(SYSTEM_TENANT_SLUG)
            assert found is not None
            return found

    async def create_tenant(
        self,
        name: str,
        slug: str,
        *,
        is_active: bool = True,
{%- if social %}
        allow_social_signup: bool = False,
{%- endif %}
    ) -> Tenant:
        """Create a tenant{% if rbac %} with its default roles{% endif %} (no users) and commit."""
        tenant = await self._stage(
            name,
            slug,
            is_active=is_active,
{%- if social %}
            allow_social_signup=allow_social_signup,
{%- endif %}
        )
        await self.session.commit()
        return tenant

    async def create_with_admin(
        self,
        name: str,
        slug: str,
        admin_email: str,
        admin_password: str,
{%- if social %}
        *,
        allow_social_signup: bool = False,
{%- endif %}
    ) -> Tenant:
        """Create the tenant{% if rbac %}, its default roles{% endif %} and its first admin in ONE transaction.

        Any failure (slug or email taken, invalid password) leaves nothing behind.
        """
        try:
            tenant = await self._stage(
                name,
                slug,
{%- if social %}
                allow_social_signup=allow_social_signup,
{%- endif %}
            )
            await UserService(self.session, tenant.id).stage(
                admin_email,
                admin_password,
{%- if rbac %}
                roles=["admin"],
{%- endif %}
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return tenant

    async def get(self, tenant_id: UUID) -> Tenant:
        tenant = await self.repo.get(tenant_id)
        if tenant is None:
            raise AppError(404, "not_found", "Tenant not found")
        return tenant

    async def list(self, limit: int, offset: int) -> tuple[Sequence[Tenant], int]:
        return await self.repo.list(limit, offset), await self.repo.count()

    async def update(self, tenant_id: UUID, changes: dict[str, Any]) -> Tenant:
        tenant = await self.get(tenant_id)
        if tenant.slug == SYSTEM_TENANT_SLUG and changes.get("is_active") is False:
            raise AppError(409, "system_tenant", "The system tenant cannot be deactivated")
        for key, value in changes.items():
            setattr(tenant, key, value)
        await self.session.commit()
        return tenant

    async def _stage(
        self,
        name: str,
        slug: str,
        *,
        is_active: bool = True,
{%- if social %}
        allow_social_signup: bool = False,
{%- endif %}
    ) -> Tenant:
        if await self.repo.get_by_slug(slug) is not None:
            raise _slug_taken()
        tenant = Tenant(
            name=name,
            slug=slug,
            is_active=is_active,
{%- if social %}
            allow_social_signup=allow_social_signup,
{%- endif %}
        )
        try:
            await self.repo.add(tenant)
        except IntegrityError as exc:  # lost a race on the unique slug index
            await self.session.rollback()
            raise _slug_taken() from exc
{%- if rbac %}
        await RbacService(self.session, tenant.id).add_default_roles()
{%- endif %}
        return tenant
