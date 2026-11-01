from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.base import Principal
from app.core.exceptions import AppError
from app.services.api_key import ApiKeyService

API_KEY_HEADER = "X-API-Key"


class ApiKeyAuthenticator:
    """``X-API-Key: <prefix>.<secret>``.

    No CSRF: browsers never attach this header on their own. No WWW-Authenticate:
    that challenge is Bearer-only. A header that is present but empty is a (bad)
    credential, not an absent one.
    """

    async def authenticate(self, request: Request, session: AsyncSession) -> Principal | None:
        raw = request.headers.get(API_KEY_HEADER)
        if raw is None:
            return None
        service = ApiKeyService(session)
        found = await service.authenticate(raw)
        if found is None:
            raise AppError(401, "invalid_credentials", "Invalid API key")
        key, owner = found
        principal = Principal(
            user_id=owner.id,
            tenant_id=None,
            is_superuser=owner.is_superuser,
            auth_method="api_key",
        )
        await service.touch(key)
        return principal
