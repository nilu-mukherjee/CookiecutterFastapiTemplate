from fastapi import APIRouter, Response

from app.api.deps import AuthServiceDep
from app.schemas.v1.auth import LoginRequest, RefreshRequest, TokenPair
from app.services.auth import IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])


def _pair(issued: IssuedTokens) -> TokenPair:
    return TokenPair(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
    )


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, service: AuthServiceDep) -> TokenPair:
    return _pair(await service.login(body.email, body.password))


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, service: AuthServiceDep) -> TokenPair:
    return _pair(await service.refresh(body.refresh_token))


@router.post("/logout", status_code=204)
async def logout(body: RefreshRequest, service: AuthServiceDep) -> Response:
    await service.logout(body.refresh_token)
    return Response(status_code=204)
