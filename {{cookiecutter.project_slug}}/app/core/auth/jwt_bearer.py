from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.access_token import invalid_token, principal_from_access_token
from app.core.auth.base import Principal

_CHALLENGE = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


class JWTBearerAuthenticator:
    """``Authorization: Bearer <access token>``."""

    async def authenticate(self, request: Request, session: AsyncSession) -> Principal | None:
        header = request.headers.get("authorization")
        if not header:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            return None  # another scheme (e.g. Basic) is another authenticator's credential
        token = token.strip()
        if not token:
            raise invalid_token(_CHALLENGE)
        return await principal_from_access_token(token, session, error_headers=_CHALLENGE)
