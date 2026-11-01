from sqlalchemy import select

from app.models.tenant import Tenant
from app.repositories.base import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    """Tenants themselves are not tenant-owned: used for tenant management
    (superuser-only endpoints) and the system tenant at startup."""

    model = Tenant

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return (await self.session.scalars(select(Tenant).where(Tenant.slug == slug))).first()
