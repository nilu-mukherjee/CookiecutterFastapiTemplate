from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import ItemServiceDep
from app.schemas.v2.item import ItemCreate, ItemPage, ItemRead, ItemUpdate

router = APIRouter(prefix="/items", tags=["items"])


@router.post("", response_model=ItemRead, status_code=201)
async def create_item(body: ItemCreate, service: ItemServiceDep) -> ItemRead:
    return ItemRead.model_validate(await service.create(body.name, body.description))


@router.get("", response_model=ItemPage)
async def list_items(
    service: ItemServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> ItemPage:
    items, next_cursor = await service.list_cursor(limit, cursor)
    return ItemPage(data=[ItemRead.model_validate(i) for i in items], next_cursor=next_cursor)


@router.get("/{item_id}", response_model=ItemRead)
async def get_item(item_id: UUID, service: ItemServiceDep) -> ItemRead:
    return ItemRead.model_validate(await service.get(item_id))


@router.patch("/{item_id}", response_model=ItemRead)
async def update_item(item_id: UUID, body: ItemUpdate, service: ItemServiceDep) -> ItemRead:
    item = await service.update(item_id, body.model_dump(exclude_unset=True))
    return ItemRead.model_validate(item)


@router.delete("/{item_id}", status_code=204)
async def delete_item(item_id: UUID, service: ItemServiceDep) -> Response:
    await service.delete(item_id)
    return Response(status_code=204)
