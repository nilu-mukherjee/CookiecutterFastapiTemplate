{%- set jwt_cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token, generate_refresh_token, hash_token
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.refresh_token import RefreshTokenRepository
from app.services.user import {% if tenancy %}UserAuthentication{% else %}UserService{% endif %}

logger = logging.getLogger("app.auth")


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    user_id: uuid.UUID
    access_token: str
    refresh_token: str
    expires_in: int


def _invalid_login() -> AppError:
    # Same error for unknown email, wrong password and inactive user.
    return AppError(401, "invalid_credentials", "Invalid email or password")


def _invalid_refresh() -> AppError:
    return AppError(401, "invalid_credentials", "Invalid or expired refresh token")


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = {% if tenancy %}UserAuthentication{% else %}UserService{% endif %}(session)
        self.tokens = RefreshTokenRepository(session)

    async def login(self, email: str, password: str) -> IssuedTokens:
        user = await self.users.authenticate(email, password)
        if user is None:
            raise _invalid_login()
        return await self.login_user(user)

    async def login_user(self, user: User) -> IssuedTokens:
        """Issue a token pair for a user who has already been authenticated."""
        issued = await self._issue(user, datetime.now(UTC))
        await self.session.commit()
        return issued

    async def refresh(self, refresh_token: str) -> IssuedTokens:
        """Rotate: revoke the presented token and issue a new pair.

        Presenting a token that was already rotated or revoked is treated as theft
        and revokes every refresh token of that user. The loser of two concurrent
        refreshes with the same token lands here too.
        """
        token_hash = hash_token(refresh_token)
        now = datetime.now(UTC)
        if not await self.tokens.revoke_if_active(token_hash, now):
            row = await self.tokens.get_by_hash(token_hash)
            if row is not None and row.revoked_at is not None:
                logger.warning("refresh token reuse detected; revoking all tokens of user")
                await self.tokens.revoke_all_for_user(row.user_id, now)
                await self.session.commit()
            raise _invalid_refresh()
        row = await self.tokens.get_by_hash(token_hash)
{%- if tenancy %}
        # None unless the user and their tenant are both still active.
        user = await self.users.get_active(row.user_id) if row is not None else None
{%- else %}
        user = await self.users.repo.get(row.user_id) if row is not None else None
{%- endif %}
        if user is None or not user.is_active:
            await self.session.commit()  # keep the revocation
            raise _invalid_refresh()
        issued = await self._issue(user, now)
        await self.session.commit()
        return issued
{%- if jwt_cookie %}

    async def refresh_token_owner(self, refresh_token: str) -> uuid.UUID | None:
        """User id of a refresh token in any state (None if never issued).

        The cookie flow needs it to check the CSRF token before refresh/logout.
        """
        row = await self.tokens.get_by_hash(hash_token(refresh_token))
        return row.user_id if row is not None else None
{%- endif %}

    async def logout(self, refresh_token: str) -> None:
        """Revoke the refresh token. Idempotent: unknown or revoked tokens are ignored."""
        await self.tokens.revoke(hash_token(refresh_token), datetime.now(UTC))
        await self.session.commit()

    async def _issue(self, user: User, now: datetime) -> IssuedTokens:
        access_token, expires_in = create_access_token(user.id)
        raw = generate_refresh_token()
        lifetime = timedelta(days=get_settings().refresh_token_expire_days)
        await self.tokens.add(
            RefreshToken(user_id=user.id, token_hash=hash_token(raw), expires_at=now + lifetime)
        )
        return IssuedTokens(
            user_id=user.id, access_token=access_token, refresh_token=raw, expires_in=expires_in
        )
