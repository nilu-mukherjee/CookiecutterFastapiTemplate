from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.access_token import principal_from_access_token
from app.core.auth.base import Principal
from app.core.auth.cookies import ACCESS_COOKIE
from app.core.auth.csrf import require_csrf


class JWTCookieAuthenticator:
    """Access token in the HttpOnly ``access_token`` cookie, plus CSRF on unsafe methods."""

    async def authenticate(self, request: Request, session: AsyncSession) -> Principal | None:
        token = request.cookies.get(ACCESS_COOKIE)
        if not token:
            return None
        # Same validation as Bearer; no WWW-Authenticate challenge for cookies.
        principal = await principal_from_access_token(token, session)
        require_csrf(request, principal.user_id)
        return principal
