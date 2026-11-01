from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.base import Principal
from app.core.auth.cookies import SESSION_COOKIE
from app.core.auth.csrf import require_csrf
from app.core.exceptions import AppError
from app.repositories.user import UserRepository
from app.services.session import SessionService


def _invalid_session() -> AppError:
    # No WWW-Authenticate: that challenge is Bearer-only.
    return AppError(401, "invalid_credentials", "Invalid or expired session")


class SessionAuthenticator:
    """Session id in the HttpOnly ``session_id`` cookie, plus CSRF on unsafe methods."""

    async def authenticate(self, request: Request, session: AsyncSession) -> Principal | None:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            return None
        service = SessionService(session)
        row = await service.resolve(raw)
        if row is None:
            raise _invalid_session()
        # Load the user on every request so deactivation takes effect immediately.
        user = await UserRepository(session).get(row.user_id)
        if user is None or not user.is_active:
            await service.revoke(row)
            raise _invalid_session()
        # After the session is validated, before it is allowed to act.
        require_csrf(request, user.id)
        principal = Principal(user_id=user.id, tenant_id=None, is_superuser=user.is_superuser)
        await service.touch(row)
        return principal
