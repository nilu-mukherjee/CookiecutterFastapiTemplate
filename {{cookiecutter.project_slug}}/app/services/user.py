import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import (
    DUMMY_HASH,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.repositories.user import UserRepository

logger = logging.getLogger("app.users")


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = UserRepository(session)

    async def create(
        self,
        email: str,
        password: str,
        *,
        is_superuser: bool = False,
        is_active: bool = True,
    ) -> User:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise AppError(
                422,
                "invalid_password",
                f"Password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters",
            )
        email = email.strip().lower()
        if await self.repo.get_by_email(email) is not None:
            raise AppError(409, "email_taken", "A user with this email already exists")
        user = User(
            email=email,
            hashed_password=hash_password(password),
            is_superuser=is_superuser,
            is_active=is_active,
        )
        try:
            await self.repo.add(user)
            await self.session.commit()
        except IntegrityError as exc:  # lost a race on the unique email index
            await self.session.rollback()
            raise AppError(409, "email_taken", "A user with this email already exists") from exc
        return user

    async def get(self, user_id: UUID) -> User:
        user = await self.repo.get(user_id)
        if user is None:
            raise AppError(404, "not_found", "User not found")
        return user

    async def list(self, limit: int, offset: int) -> tuple[Sequence[User], int]:
        return await self.repo.list(limit, offset), await self.repo.count()

    async def authenticate(self, email: str, password: str) -> User | None:
        """Return the user for valid credentials of an active account, else None.

        A hash is verified on every path (a dummy one for unknown emails) so the
        response time does not reveal whether the account exists.
        """
        user = await self.repo.get_by_email(email)
        if user is None:
            verify_password(password, DUMMY_HASH)
            return None
        if not verify_password(password, user.hashed_password) or not user.is_active:
            return None
        return user

    async def bootstrap_superuser(self) -> User | None:
        """Create the first superuser from settings if configured and absent."""
        settings = get_settings()
        email, password = settings.first_superuser_email, settings.first_superuser_password
        if not email or not password:
            return None
        if await self.repo.get_by_email(email) is not None:
            return None
        try:
            return await self.create(email, password, is_superuser=True)
        except AppError as exc:
            if exc.code != "email_taken":
                raise
            # Another worker created it between our check and insert (concurrent startup).
            await self.session.rollback()
            logger.info("first superuser already created by another process")
            return None
