"""Shared by the JWT methods: turn an access token into a Principal."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.base import Principal
from app.core.exceptions import AppError
from app.core.security import InvalidTokenError, decode_access_token
from app.repositories.user import UserRepository


def invalid_token(headers: dict[str, str] | None = None) -> AppError:
    return AppError(401, "invalid_credentials", "Invalid or expired token", headers=headers)


async def principal_from_access_token(
    token: str, session: AsyncSession, *, error_headers: dict[str, str] | None = None
) -> Principal:
    """Verify the access token and load its (active) user, or raise a 401.

    ``error_headers`` lets each method add its own challenge (Bearer sends
    ``WWW-Authenticate``; the cookie method sends none).
    """
    try:
        claims = decode_access_token(token)
    except InvalidTokenError as exc:
        raise invalid_token(error_headers) from exc
    # Load the user on every request so deactivation takes effect immediately.
    user = await UserRepository(session).get(claims.user_id)
    if user is None or not user.is_active:
        raise invalid_token(error_headers)
    return Principal(user_id=user.id, tenant_id=None, is_superuser=user.is_superuser)
