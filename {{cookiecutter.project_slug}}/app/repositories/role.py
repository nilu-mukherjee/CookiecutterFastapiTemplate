import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import selectinload

from app.models.permission import Permission
from app.models.role import Role, role_permissions, user_roles
from app.repositories.base import BaseRepository


class PermissionRepository(BaseRepository[Permission]):
    model = Permission

    async def get_by_names(self, names: Sequence[str]) -> Sequence[Permission]:
        stmt = select(Permission).where(Permission.name.in_(names)).order_by(Permission.name)
        return (await self.session.scalars(stmt)).all()


class RoleRepository(BaseRepository[Role]):
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
