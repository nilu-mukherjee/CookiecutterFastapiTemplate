from uuid import UUID

from fastapi import APIRouter

from app.api.deps import RbacServiceDep, UserServiceDep, UsersReader, UsersWriter
from app.core.exceptions import AppError
from app.schemas.v1.role import RoleList, RoleRead, UserRolesUpdate
from app.schemas.v1.user import UserRead

router = APIRouter(tags=["roles"])


@router.get("/roles", response_model=RoleList)
async def list_roles(rbac: RbacServiceDep, _: UsersReader) -> RoleList:
    roles = await rbac.list_roles()
    return RoleList(
        items=[
            RoleRead(id=r.id, name=r.name, permissions=sorted(p.name for p in r.permissions))
            for r in roles
        ]
    )


@router.put("/users/{user_id}/roles", response_model=UserRead)
async def set_user_roles(
    user_id: UUID,
    body: UserRolesUpdate,
    principal: UsersWriter,
    users: UserServiceDep,
    rbac: RbacServiceDep,
) -> UserRead:
    """Replace the user's roles.

    Only a superuser may change their own roles: otherwise anyone holding ``users:write``
    through a low-trust credential could escalate themselves (or lock themselves out).
    """
    if user_id == principal.user_id and not principal.is_superuser:
        raise AppError(403, "cannot_modify_own_roles", "You cannot change your own roles")
    user = await users.get(user_id)
    names = await rbac.set_user_roles(user.id, body.roles)
    return UserRead.model_validate(user).model_copy(update={"roles": names})
