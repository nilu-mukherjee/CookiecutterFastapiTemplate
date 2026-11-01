{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import delete, insert, select{% if tenancy %}, true{% endif %}
from sqlalchemy.orm import selectinload

from app.models.permission import Permission
from app.models.role import Role, role_permissions, user_roles
{% if tenancy %}from app.models.user import User
from app.repositories.base import BaseRepository, TenantScopedRepository
{% else %}from app.repositories.base import BaseRepository
{% endif %}

class PermissionRepository(BaseRepository[Permission]):
    model = Permission

    async def get_by_names(self, names: Sequence[str]) -> Sequence[Permission]:
        stmt = select(Permission).where(Permission.name.in_(names)).order_by(Permission.name)
        return (await self.session.scalars(stmt)).all()


{% if tenancy %}class RoleRepository(TenantScopedRepository[Role]):
    """Roles of ONE tenant. The user-role links are only read or written through this
    tenant's roles, so a role of another tenant can neither be granted nor counted."""

    model = Role

    async def get_by_name(self, name: str) -> Role | None:
        stmt = (
            self._scoped(select(Role))
            .where(Role.name == name)
            .options(selectinload(Role.permissions))
        )
        return (await self.session.scalars(stmt)).one_or_none()

    async def get_by_names(self, names: Sequence[str]) -> Sequence[Role]:
        stmt = self._scoped(select(Role)).where(Role.name.in_(names)).order_by(Role.name)
        return (await self.session.scalars(stmt)).all()

    async def list_all(self) -> Sequence[Role]:
        stmt = (
            self._scoped(select(Role))
            .options(selectinload(Role.permissions))
            .order_by(Role.name)
        )
        return (await self.session.scalars(stmt)).all()

    async def permission_names_for_user(self, user_id: uuid.UUID) -> set[str]:
        stmt = (
            select(Permission.name)
            .join(role_permissions, role_permissions.c.permission_id == Permission.id)
            .join(user_roles, user_roles.c.role_id == role_permissions.c.role_id)
            .join(Role, Role.id == user_roles.c.role_id)
            .where(user_roles.c.user_id == user_id, Role.tenant_id == self.tenant_id)
        )
        return set((await self.session.scalars(stmt)).all())

    async def role_names_for_users(
        self, user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        stmt = (
            select(user_roles.c.user_id, Role.name)
            .join(Role, Role.id == user_roles.c.role_id)
            .where(user_roles.c.user_id.in_(user_ids), Role.tenant_id == self.tenant_id)
            .order_by(Role.name)
        )
        names: dict[uuid.UUID, list[str]] = defaultdict(list)
        for user_id, name in (await self.session.execute(stmt)).all():
            names[user_id].append(name)
        return dict(names)

    async def replace_user_roles(self, user_id: uuid.UUID, roles: Sequence[Role]) -> None:
        """Replace the user's roles in this tenant.

        Both statements are filtered in SQL: only links to this tenant's roles are
        removed, and a link is only inserted for a user AND a role of this tenant.
        """
        own_roles = select(Role.id).where(Role.tenant_id == self.tenant_id)
        await self.session.execute(
            delete(user_roles).where(
                user_roles.c.user_id == user_id, user_roles.c.role_id.in_(own_roles)
            )
        )
        if roles:
            pairs = (
                select(User.id, Role.id)
                .select_from(User)
                .join(Role, true())  # every (user, role) pair, then filtered
                .where(User.id == user_id, User.tenant_id == self.tenant_id)
                .where(Role.id.in_([r.id for r in roles]), Role.tenant_id == self.tenant_id)
            )
            await self.session.execute(
                insert(user_roles).from_select(["user_id", "role_id"], pairs)
            )
        await self.session.flush()
{%- else %}class RoleRepository(BaseRepository[Role]):
    model = Role

    async def get_by_name(self, name: str) -> Role | None:
        stmt = select(Role).where(Role.name == name).options(selectinload(Role.permissions))
        return (await self.session.scalars(stmt)).one_or_none()

    async def get_by_names(self, names: Sequence[str]) -> Sequence[Role]:
        stmt = select(Role).where(Role.name.in_(names)).order_by(Role.name)
        return (await self.session.scalars(stmt)).all()

    async def list_all(self) -> Sequence[Role]:
        stmt = select(Role).options(selectinload(Role.permissions)).order_by(Role.name)
        return (await self.session.scalars(stmt)).all()

    async def permission_names_for_user(self, user_id: uuid.UUID) -> set[str]:
        stmt = (
            select(Permission.name)
            .join(role_permissions, role_permissions.c.permission_id == Permission.id)
            .join(user_roles, user_roles.c.role_id == role_permissions.c.role_id)
            .where(user_roles.c.user_id == user_id)
        )
        return set((await self.session.scalars(stmt)).all())

    async def role_names_for_users(
        self, user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        stmt = (
            select(user_roles.c.user_id, Role.name)
            .join(Role, Role.id == user_roles.c.role_id)
            .where(user_roles.c.user_id.in_(user_ids))
            .order_by(Role.name)
        )
        names: dict[uuid.UUID, list[str]] = defaultdict(list)
        for user_id, name in (await self.session.execute(stmt)).all():
            names[user_id].append(name)
        return dict(names)

    async def replace_user_roles(self, user_id: uuid.UUID, roles: Sequence[Role]) -> None:
        await self.session.execute(delete(user_roles).where(user_roles.c.user_id == user_id))
        if roles:
            await self.session.execute(
                insert(user_roles), [{"user_id": user_id, "role_id": r.id} for r in roles]
            )
        await self.session.flush()
{%- endif %}
