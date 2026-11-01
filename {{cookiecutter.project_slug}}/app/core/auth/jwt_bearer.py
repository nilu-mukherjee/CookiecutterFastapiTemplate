from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.base import Principal
from app.core.exceptions import AppError
from app.core.security import InvalidTokenError, decode_access_token
from app.repositories.user import UserRepository

_CHALLENGE = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


def _invalid() -> AppError:
    return AppError(401, "invalid_credentials", "Invalid or expired token", headers=_CHALLENGE)


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
            raise _invalid()
        try:
            claims = decode_access_token(token)
        except InvalidTokenError as exc:
            raise _invalid() from exc
        # Load the user on every request so deactivation takes effect immediately.
        user = await UserRepository(session).get(claims.user_id)
        if user is None or not user.is_active:
            raise _invalid()
        return Principal(user_id=user.id, tenant_id=None, is_superuser=user.is_superuser)
