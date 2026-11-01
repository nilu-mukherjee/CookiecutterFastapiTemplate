{%- set rbac = cookiecutter.use_rbac == "y" -%}
{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
from typing import Annotated

{% if rbac %}from fastapi import APIRouter, Query

from app.api.deps import (
    CurrentPrincipal,
    RbacServiceDep,
    UserServiceDep,
    UsersReader,
    UsersWriter,
)
from app.core.exceptions import AppError
from app.models.user import User
{% else %}from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, SuperuserPrincipal, UserServiceDep
{% endif %}from app.schemas.v1.user import UserCreate, UserList, UserRead
{%- if rbac %}
from app.services.rbac import RbacService
{%- endif %}

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=201)
async def create_user(
{%- if rbac %}
    body: UserCreate, service: UserServiceDep, rbac: RbacServiceDep, principal: UsersWriter
) -> UserRead:
    """Needs ``users:write``: there is no open registration.

    Creating a superuser (who bypasses every permission check) stays superuser-only.
{%- if tenancy %}
    The user is created in the caller's tenant, with roles of that tenant only; a
    superuser can only be created from the system tenant (403 ``forbidden`` otherwise).
{%- endif %}
    """
    if body.is_superuser and not principal.is_superuser:
        raise AppError(403, "forbidden", "Only a superuser can create a superuser")
    user = await service.create(
        body.email, body.password, is_superuser=body.is_superuser, roles=body.roles
    )
    return await _read(user, rbac)
{%- else %}
    body: UserCreate, service: UserServiceDep, _: SuperuserPrincipal
) -> UserRead:
{%- if tenancy %}
    """Superuser only: there is no open registration.

    The user is created in the caller's tenant; superusers live in the system tenant,
    which is the only tenant where another superuser can be created.
    """
{%- else %}
    """Superuser only: there is no open registration."""
{%- endif %}
    user = await service.create(body.email, body.password, is_superuser=body.is_superuser)
    return UserRead.model_validate(user)
{%- endif %}


@router.get("/me", response_model=UserRead)
async def read_me(
    principal: CurrentPrincipal, service: UserServiceDep{% if rbac %}, rbac: RbacServiceDep{% endif %}
) -> UserRead:
{%- if rbac %}
    return await _read(await service.get(principal.user_id), rbac)
{%- else %}
    return UserRead.model_validate(await service.get(principal.user_id))
{%- endif %}


@router.get("", response_model=UserList)
async def list_users(
    service: UserServiceDep,
{%- if rbac %}
    rbac: RbacServiceDep,
    _: UsersReader,
{%- else %}
    _: SuperuserPrincipal,
{%- endif %}
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserList:
    users, total = await service.list(limit, offset)
{%- if rbac %}
    roles = await rbac.role_names([u.id for u in users])
    items = [
        UserRead.model_validate(u).model_copy(update={"roles": roles.get(u.id, [])})
        for u in users
    ]
{%- else %}
    items = [UserRead.model_validate(u) for u in users]
{%- endif %}
    return UserList(items=items, total=total, limit=limit, offset=offset)
{%- if rbac %}


async def _read(user: User, rbac: RbacService) -> UserRead:
    roles = (await rbac.role_names([user.id])).get(user.id, [])
    return UserRead.model_validate(user).model_copy(update={"roles": roles})
{%- endif %}
