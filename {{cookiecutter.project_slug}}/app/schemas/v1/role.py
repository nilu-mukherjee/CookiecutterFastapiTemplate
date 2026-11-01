from uuid import UUID

from pydantic import BaseModel, Field


class RoleRead(BaseModel):
    id: UUID
    name: str
    permissions: list[str]


class RoleList(BaseModel):
    items: list[RoleRead]


class UserRolesUpdate(BaseModel):
    """The complete set of roles the user should have (replaces the current set)."""

    roles: list[str] = Field(max_length=50)
