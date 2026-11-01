from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class ApiKeyCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    # Omitted: the key never expires.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyRead(BaseModel):
    """A key as listed: never the secret and never its hash."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


class ApiKeyCreated(BaseModel):
    """The creation response: the only place the raw key ever appears."""

    id: UUID
    name: str
    prefix: str
    # ``<prefix>.<secret>``; it cannot be retrieved again.
    key: str
    expires_at: datetime | None
    created_at: datetime


class ApiKeyList(BaseModel):
    items: list[ApiKeyRead]
    total: int
    limit: int
    offset: int
