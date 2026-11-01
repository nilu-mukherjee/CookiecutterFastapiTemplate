import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import time as clock
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.session import Session
from app.repositories.session import SessionRepository
from app.services.user import UserService

# last_seen_at is only rewritten when older than this: no write per request.
TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user_id: UUID
    raw_id: str  # goes in the cookie only; never stored or logged
    expires_in: int  # seconds until the absolute expiry


def hash_session_id(raw: str) -> str:
    """SHA-256 is enough: the id is 256 random bits (no dictionary to brute-force)."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    # SQLite hands timezone-aware columns back naive (always UTC here).
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SessionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserService(session)
        self.repo = SessionRepository(session)

    async def login(self, email: str, password: str, presented: str | None) -> IssuedSession:
        """Verify the credentials and start a NEW session.

        A session id presented with the request is never reused (session
        fixation); if it belongs to an existing session, that session is deleted.
        """
        user = await self.users.authenticate(email, password)
        if user is None:
            # Same error for unknown email, wrong password and inactive user.
            raise AppError(401, "invalid_credentials", "Invalid email or password")
        if presented:
            await self.repo.delete_by_hash(hash_session_id(presented))
        issued = await self._create(user.id)
        await self.session.commit()
        return issued

    async def resolve(self, raw: str) -> Session | None:
        """The live session for this id, or None. Dead rows are deleted when met."""
        row = await self.repo.get_by_hash(hash_session_id(raw))
        if row is None:
            return None
        now = clock.utcnow()
        settings = get_settings()
        idle = timedelta(minutes=settings.session_idle_timeout_minutes)
        if now >= _aware(row.expires_at) or now - _aware(row.last_seen_at) >= idle:
            await self.revoke(row)
            return None
        return row

    async def touch(self, row: Session) -> None:
        """Slide the idle window, writing at most once per TOUCH_INTERVAL."""
        now = clock.utcnow()
        if now - _aware(row.last_seen_at) > TOUCH_INTERVAL:
            await self.repo.set_last_seen(row.id, now)
            await self.session.commit()

    async def revoke(self, row: Session) -> None:
        await self.repo.delete(row)
        await self.session.commit()

    async def _create(self, user_id: UUID) -> IssuedSession:
        now = clock.utcnow()
        lifetime = timedelta(hours=get_settings().session_absolute_lifetime_hours)
        raw = secrets.token_urlsafe(32)
        await self.repo.add(
            Session(
                user_id=user_id,
                token_hash=hash_session_id(raw),
                created_at=now,
                last_seen_at=now,
                expires_at=now + lifetime,
            )
        )
        return IssuedSession(user_id=user_id, raw_id=raw, expires_in=int(lifetime.total_seconds()))
