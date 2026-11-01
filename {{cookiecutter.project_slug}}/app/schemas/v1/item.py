from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_null(cls, value: str | None) -> str | None:
        # name is NOT NULL in the database: reject an explicit null, allow omission.
        if value is None:
            raise ValueError("name must not be null")
        return value


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    created_at: datetime


class ItemList(BaseModel):
    items: list[ItemRead]
    total: int
    limit: int
    offset: int
