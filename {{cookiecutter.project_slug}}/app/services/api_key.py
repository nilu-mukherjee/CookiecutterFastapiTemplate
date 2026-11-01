import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import time as clock
from app.core.exceptions import AppError
from app.models.api_key import ApiKey
from app.models.user import User
from app.repositories.api_key import ApiKeyRepository
from app.repositories.user import UserRepository

MAX_ACTIVE_KEYS = 20
# last_used_at is only rewritten when older than this: no write per request.
TOUCH_INTERVAL = timedelta(seconds=60)
_PREFIX = re.compile(r"[0-9a-f]{8}")
_MAX_RAW_LENGTH = 256
_PREFIX_ATTEMPTS = 5


def hash_secret(secret: str) -> str:
    """SHA-256 is enough: the secret is 256 random bits (no dictionary to brute-force)."""
    return hashlib.sha256(secret.encode()).hexdigest()


# Compared against when the prefix is unknown or malformed, so those requests cost
# the same as a wrong secret (no way to tell which prefixes exist).
_DUMMY_KEY_HASH = hash_secret("dummy-secret-used-only-for-timing-equalisation")


def _aware(value: datetime) -> datetime:
    # SQLite hands timezone-aware columns back naive (always UTC here).
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _split(raw: str) -> tuple[str | None, str]:
    """``(prefix, secret)``; the prefix is None when the key cannot be one of ours."""
    prefix, sep, secret = raw.partition(".")
    if not sep or len(raw) > _MAX_RAW_LENGTH or not _PREFIX.fullmatch(prefix):
        return None, raw
    return prefix, secret


class ApiKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ApiKeyRepository(session)

    async def create(
        self, user_id: UUID, name: str, expires_in_days: int | None = None
    ) -> tuple[ApiKey, str]:
        """Create a key and return it with the raw ``<prefix>.<secret>``.

        The raw key exists only in this return value: it is neither stored nor logged.
        """
        now = clock.utcnow()
        if await self.repo.count_active(user_id, now) >= MAX_ACTIVE_KEYS:
            raise AppError(
                409, "api_key_limit", f"At most {MAX_ACTIVE_KEYS} active API keys per user"
            )
        expires_at = now + timedelta(days=expires_in_days) if expires_in_days else None
        secret = secrets.token_urlsafe(32)
        for _ in range(_PREFIX_ATTEMPTS):
            prefix = secrets.token_hex(4)
            key = ApiKey(
                user_id=user_id,
                name=name,
                prefix=prefix,
                key_hash=hash_secret(secret),
                created_at=now,
                expires_at=expires_at,
            )
            try:
                await self.repo.add(key)
                await self.session.commit()
            except IntegrityError:  # the 32-bit prefix collided with an existing key
                await self.session.rollback()
                continue
            return key, f"{prefix}.{secret}"
        raise AppError(500, "internal_error", "Could not allocate an API key prefix")

    async def list_for_user(
        self, user_id: UUID, limit: int, offset: int
    ) -> tuple[list[ApiKey], int]:
        return (
            await self.repo.list_for_user(user_id, limit, offset),
            await self.repo.count_for_user(user_id),
        )

    async def revoke(self, user_id: UUID, key_id: UUID) -> None:
        """Revoke the user's own key (idempotent); another user's id is a 404."""
        key = await self.repo.get_for_user(user_id, key_id)
        if key is None:
            raise AppError(404, "not_found", "API key not found")
        if key.revoked_at is None:
            key.revoked_at = clock.utcnow()
            await self.session.commit()

    async def authenticate(self, raw: str) -> tuple[ApiKey, User] | None:
        """The key and its (active) owner for a valid key, else None.

        A digest comparison happens on every path (against a dummy for an unknown
        or malformed prefix), and it is constant-time.
        """
        prefix, secret = _split(raw)
        key = await self.repo.get_by_prefix(prefix) if prefix else None
        expected = key.key_hash if key is not None else _DUMMY_KEY_HASH
        matches = hmac.compare_digest(hash_secret(secret), expected)
        if key is None or not matches:
            return None
        if key.revoked_at is not None:
            return None
        if key.expires_at is not None and clock.utcnow() >= _aware(key.expires_at):
            return None
        # Load the owner on every request so deactivation takes effect immediately.
        owner = await UserRepository(self.session).get(key.user_id)
        if owner is None or not owner.is_active:
            return None
        return key, owner

    async def touch(self, key: ApiKey) -> None:
        """Record the use, writing at most once per TOUCH_INTERVAL."""
        now = clock.utcnow()
        if key.last_used_at is None or now - _aware(key.last_used_at) >= TOUCH_INTERVAL:
            await self.repo.set_last_used(key.id, now)
            await self.session.commit()
