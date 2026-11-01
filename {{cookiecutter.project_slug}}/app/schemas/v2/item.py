from pydantic import BaseModel

from app.schemas.v1.item import ItemCreate, ItemRead, ItemUpdate

__all__ = ["ItemCreate", "ItemRead", "ItemUpdate", "ItemPage"]


class ItemPage(BaseModel):
    data: list[ItemRead]
    next_cursor: str | None
