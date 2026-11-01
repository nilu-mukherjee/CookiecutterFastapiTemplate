from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    is_superuser: bool = False


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    is_active: bool
    is_superuser: bool
    created_at: datetime


class UserList(BaseModel):
    items: list[UserRead]
    total: int
    limit: int
    offset: int
