from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making the request, whatever method authenticated them."""

    user_id: UUID
    tenant_id: UUID | None
    is_superuser: bool


class Authenticator(Protocol):
    """One authentication method (Bearer, API key, session cookie, ...).

    ``authenticate`` returns None when the request carries no credential of this
    kind, a Principal when it carries a valid one, and raises
    ``AppError(401, "invalid_credentials", ...)`` when it carries an invalid one.
    Raising (rather than returning None) stops the next method from being tried.
    """

    async def authenticate(self, request: Request, session: AsyncSession) -> Principal | None:
        ...
