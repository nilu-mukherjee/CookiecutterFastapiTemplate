{%- set cookie = cookiecutter.use_auth == "y" and cookiecutter.use_auth_jwt_cookie == "y" -%}
from collections.abc import Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import hash_password, verify_password
from app.models import User
from app.services.user import UserService
from tests.conftest import MakeUser

STRONG = "x" * 40


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_password_stored_as_argon2_hash(
    make_user: MakeUser, maker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user("hash@example.com", "a-long-password")
    async with maker() as session:
        user = (await session.scalars(select(User))).one()
    assert user.hashed_password.startswith("$argon2id$")
    assert "a-long-password" not in user.hashed_password
    assert verify_password("a-long-password", user.hashed_password)


def test_verify_password_returns_bool_and_never_raises() -> None:
    hashed = hash_password("secret-password")
    assert verify_password("secret-password", hashed) is True
    assert verify_password("wrong-password", hashed) is False
    assert verify_password("secret-password", "not-a-hash") is False
    assert verify_password("secret-password", "") is False


def test_settings_reject_weak_secret_outside_dev() -> None:
    for env in ("staging", "prod"):
        for weak in ("change-me", "short-secret"):
            with pytest.raises(ValidationError):
                Settings(_env_file=None, environment=env, secret_key=weak)
        Settings(_env_file=None, environment=env, secret_key=STRONG{% if cookie %}, cookie_secure=True{% endif %})
    Settings(_env_file=None, environment="dev", secret_key="change-me")


def test_settings_error_does_not_echo_the_secret() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, environment="prod", secret_key="short-real-secret")
    assert "short-real-secret" not in str(exc.value)


async def test_email_stored_lowercase_and_unique_case_insensitive(make_user: MakeUser) -> None:
    user = await make_user("Mixed.Case@Example.COM")
    assert user.email == "mixed.case@example.com"
    with pytest.raises(AppError) as exc:
        await make_user("MIXED.case@example.com")
    assert exc.value.status_code == 409


async def test_password_minimum_length_enforced(make_user: MakeUser) -> None:
    with pytest.raises(AppError) as exc:
        await make_user("short@example.com", "123456789")
    assert exc.value.status_code == 422


async def test_bootstrap_superuser_creates_once(
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    monkeypatch.setenv("FIRST_SUPERUSER_EMAIL", "Admin@Example.com")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "bootstrap-password")
    get_settings.cache_clear()
    async with maker() as session:
        created = await UserService(session).bootstrap_superuser()
    assert created is not None
    assert created.is_superuser
    assert created.email == "admin@example.com"
    async with maker() as session:
        assert await UserService(session).bootstrap_superuser() is None
        users = (await session.scalars(select(User))).all()
    assert len(users) == 1


async def test_bootstrap_concurrent_loser_is_noop(
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    """Two workers starting together: the loser must not crash startup."""
    monkeypatch.setenv("FIRST_SUPERUSER_EMAIL", "admin@example.com")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "bootstrap-password")
    get_settings.cache_clear()
    async with maker() as winner_session, maker() as loser_session:
        loser = UserService(loser_session)

        async def not_found_yet(email: str) -> None:
            return None  # the loser checked before the winner committed

        monkeypatch.setattr(loser.repo, "get_by_email", not_found_yet)
        assert await UserService(winner_session).bootstrap_superuser() is not None
        # The loser now hits the real unique-index IntegrityError path.
        assert await loser.bootstrap_superuser() is None
    async with maker() as session:
        users = (await session.scalars(select(User))).all()
    assert [u.email for u in users] == ["admin@example.com"]


async def test_bootstrap_other_errors_still_raise(
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    monkeypatch.setenv("FIRST_SUPERUSER_EMAIL", "admin@example.com")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "too-short")
    get_settings.cache_clear()
    async with maker() as session:
        with pytest.raises(AppError) as exc:
            await UserService(session).bootstrap_superuser()
    assert exc.value.code == "invalid_password"

    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "bootstrap-password")
    get_settings.cache_clear()
    async with maker() as session:
        service = UserService(session)

        async def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("db down")

        monkeypatch.setattr(service, "create", boom)
        with pytest.raises(RuntimeError):
            await service.bootstrap_superuser()


async def test_bootstrap_superuser_noop_when_not_configured(
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    monkeypatch.delenv("FIRST_SUPERUSER_EMAIL", raising=False)
    monkeypatch.delenv("FIRST_SUPERUSER_PASSWORD", raising=False)
    async with maker() as session:
        assert await UserService(session).bootstrap_superuser() is None
        assert (await session.scalars(select(User))).all() == []
