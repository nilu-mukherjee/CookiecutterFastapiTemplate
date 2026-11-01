from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import ApiKeyServiceDep, HumanPrincipal
from app.schemas.v1.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyList, ApiKeyRead

# Every route takes HumanPrincipal: a request authenticated by an API key is refused,
# so a leaked key cannot mint replacements or revoke its siblings.
router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    body: ApiKeyCreate,
    response: Response,
    service: ApiKeyServiceDep,
    principal: HumanPrincipal,
) -> ApiKeyCreated:
    """Create a key for the caller. The raw ``key`` is only in this response."""
    key, raw = await service.create(principal.user_id, body.name, body.expires_in_days)
    response.headers["Cache-Control"] = "no-store"
    return ApiKeyCreated(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        key=raw,
        expires_at=key.expires_at,
        created_at=key.created_at,
    )


@router.get("", response_model=ApiKeyList)
async def list_api_keys(
    service: ApiKeyServiceDep,
    principal: HumanPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApiKeyList:
    keys, total = await service.list_for_user(principal.user_id, limit, offset)
    return ApiKeyList(
        items=[ApiKeyRead.model_validate(k) for k in keys],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: UUID, service: ApiKeyServiceDep, principal: HumanPrincipal
) -> Response:
    await service.revoke(principal.user_id, key_id)
    return Response(status_code=204)
