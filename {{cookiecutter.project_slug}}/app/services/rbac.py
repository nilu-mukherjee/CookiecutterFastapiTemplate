import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.permission import Permission
from app.models.role import Role
from app.repositories.role import PermissionRepository, RoleRepository

logger = logging.getLogger("app.rbac")

PERMISSIONS = ("items:read", "items:write", "users:read", "users:write")
DEFAULT_ROLE = "member"
# Applied only when a role is first created; later edits to a role are never overwritten.
DEFAULT_ROLES: dict[str, tuple[str, ...]] = {
    "admin": PERMISSIONS,
    DEFAULT_ROLE: ("items:read", "items:write"),
}


class RbacService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.permissions = PermissionRepository(session)
        self.roles = RoleRepository(session)

    # --- seeding (idempotent, safe when several workers start at once) -----------------

    async def ensure_permissions(self) -> None:
        existing = {p.name for p in await self.permissions.get_by_names(PERMISSIONS)}
        missing = [name for name in PERMISSIONS if name not in existing]
        if not missing:
            return
        try:
            for name in missing:
                self.session.add(Permission(name=name))
            await self.session.commit()
        except IntegrityError:
            # Another worker seeded them between our check and insert: nothing to do.
            await self.session.rollback()
            logger.info("permissions already seeded by another process")

    async def ensure_default_roles(self) -> None:
        await self.ensure_permissions()
        by_name = {p.name: p for p in await self.permissions.get_by_names(PERMISSIONS)}
        for role_name, permission_names in DEFAULT_ROLES.items():
            if await self.roles.get_by_name(role_name) is not None:
                continue
            try:
                self.session.add(
                    Role(name=role_name, permissions=[by_name[n] for n in permission_names])
                )
                await self.session.commit()
            except IntegrityError:
                await self.session.rollback()
                logger.info("role %s already created by another process", role_name)

    # --- lookups -----------------------------------------------------------------------

    async def list_roles(self) -> Sequence[Role]:
        return await self.roles.list_all()

    async def permissions_for(self, user_id: uuid.UUID) -> set[str]:
        """The user's permission names, read from the database (never cached across requests)."""
        return await self.roles.permission_names_for_user(user_id)

    async def role_names(self, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        return await self.roles.role_names_for_users(user_ids)

    async def resolve_roles(self, names: Sequence[str]) -> list[Role]:
        """Roles for the names (duplicates ignored); AppError 422 for any unknown name.

        A missing default role is re-seeded first, so a fresh or partly wiped database
        still lets users be created. Seeding commits: call this before starting a write.
        """
        wanted = sorted(set(names))
        found = {r.name: r for r in await self.roles.get_by_names(wanted)}
        if any(n not in found and n in DEFAULT_ROLES for n in wanted):
            await self.ensure_default_roles()
            found = {r.name: r for r in await self.roles.get_by_names(wanted)}
        unknown = [n for n in wanted if n not in found]
        if unknown:
            raise AppError(422, "unknown_role", f"Unknown role: {', '.join(unknown)}")
        return [found[n] for n in wanted]

    # --- assignment --------------------------------------------------------------------

    async def assign(self, user_id: uuid.UUID, roles: Sequence[Role]) -> None:
        """Replace the user's roles in the current transaction (the caller commits)."""
        await self.roles.replace_user_roles(user_id, roles)

    async def set_user_roles(self, user_id: uuid.UUID, names: Sequence[str]) -> list[str]:
        """Replace the user's roles with the named ones and commit; returns the sorted names."""
        roles = await self.resolve_roles(names)
        await self.assign(user_id, roles)
        await self.session.commit()
        return [r.name for r in roles]

    async def grant_default_roles(self, user_id: uuid.UUID) -> None:
        """Give an existing user the default ``member`` role (keeping any other roles)."""
        current = await self.role_names([user_id])
        await self.set_user_roles(user_id, [*current.get(user_id, []), DEFAULT_ROLE])
