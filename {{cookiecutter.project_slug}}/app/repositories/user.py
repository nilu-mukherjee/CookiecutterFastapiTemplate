from sqlalchemy import select

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        """Emails are stored lowercased; the lookup lowercases too."""
        stmt = select(User).where(User.email == email.strip().lower())
        return (await self.session.scalars(stmt)).one_or_none()
