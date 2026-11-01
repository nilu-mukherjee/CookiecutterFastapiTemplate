from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, SuperuserPrincipal, UserServiceDep
from app.schemas.v1.user import UserCreate, UserList, UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=201)
async def create_user(
    body: UserCreate, service: UserServiceDep, _: SuperuserPrincipal
) -> UserRead:
    """Superuser only: there is no open registration."""
    user = await service.create(body.email, body.password, is_superuser=body.is_superuser)
    return UserRead.model_validate(user)


@router.get("/me", response_model=UserRead)
async def read_me(principal: CurrentPrincipal, service: UserServiceDep) -> UserRead:
    return UserRead.model_validate(await service.get(principal.user_id))


@router.get("", response_model=UserList)
async def list_users(
    service: UserServiceDep,
    _: SuperuserPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserList:
    users, total = await service.list(limit, offset)
    return UserList(
        items=[UserRead.model_validate(u) for u in users],
        total=total,
        limit=limit,
        offset=offset,
    )
