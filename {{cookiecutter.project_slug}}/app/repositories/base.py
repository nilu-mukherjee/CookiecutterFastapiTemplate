{%- set tenancy = cookiecutter.use_auth == "y" and cookiecutter.use_multitenancy == "y" -%}
import uuid
from collections.abc import Sequence
from typing import {% if tenancy %}Any, {% endif %}Generic, TypeVar

{% if tenancy %}from sqlalchemy import Select, delete, func, select
{% else %}from sqlalchemy import func, select
{% endif %}from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Entity{% if tenancy %}, TenantEntity{% endif %}

T = TypeVar("T", bound=Entity)


class BaseRepository(Generic[T]):  # noqa: UP046
    model: type[T]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id: uuid.UUID) -> T | None:
        return await self.session.get(self.model, id)

    async def list(self, limit: int, offset: int) -> Sequence[T]:
        stmt = (
            select(self.model)
            .order_by(self.model.created_at, self.model.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def count(self) -> int:
        result = await self.session.scalar(select(func.count()).select_from(self.model))
        return result or 0

    async def add(self, obj: T) -> T:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete(self, obj: T) -> None:
        await self.session.delete(obj)
        await self.session.flush()
{%- if tenancy %}


TT = TypeVar("TT", bound=TenantEntity)
Stmt = TypeVar("Stmt", bound=Select[Any])


class TenantScopedRepository(BaseRepository[TT]):
    """Repository of a tenant-owned model: every query is limited to ONE tenant.

    The tenant is an explicit, required constructor argument (``Principal.tenant_id``,
    passed down by the endpoint/service layer), never read from the request. Every
    statement goes through ``_scoped`` (the shared tenant filter), so another tenant's
    row behaves exactly like a missing one: ``get`` returns None and the endpoint
    answers 404. ``add`` overwrites whatever tenant_id the object carries. Subclasses
    build their queries as ``self._scoped(select(...))``; tests/tenancy/test_meta.py
    fails for any public method whose SQL lacks the tenant filter.
    """

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        if not isinstance(tenant_id, uuid.UUID):
            raise TypeError("TenantScopedRepository needs the tenant id (a UUID)")
        super().__init__(session)
        self.tenant_id = tenant_id

    def _scoped(self, stmt: Stmt) -> Stmt:
        """The statement limited to this repository's tenant (the shared tenant filter)."""
        return stmt.where(self.model.tenant_id == self.tenant_id)

    async def get(self, id: uuid.UUID) -> TT | None:
        # Not session.get(): the identity map would bypass the tenant filter.
        stmt = self._scoped(select(self.model)).where(self.model.id == id)
        return (await self.session.scalars(stmt)).first()

    async def list(self, limit: int, offset: int) -> Sequence[TT]:
        stmt = (
            self._scoped(select(self.model))
            .order_by(self.model.created_at, self.model.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def count(self) -> int:
        stmt = self._scoped(select(func.count()).select_from(self.model))
        return await self.session.scalar(stmt) or 0

    async def add(self, obj: TT) -> TT:
        obj.tenant_id = self.tenant_id  # never trust a tenant_id set by the caller
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete(self, obj: TT) -> None:
        await self.session.execute(
            delete(self.model).where(
                self.model.id == obj.id, self.model.tenant_id == self.tenant_id
            )
        )
        await self.session.flush()
{%- endif %}
