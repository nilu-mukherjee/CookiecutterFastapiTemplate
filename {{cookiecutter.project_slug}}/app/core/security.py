{%- set use_jwt = (cookiecutter.use_auth_jwt_bearer == "y" or cookiecutter.use_auth_jwt_cookie == "y") -%}
"""Password hashing{% if use_jwt %}, access tokens (JWT) and refresh-token helpers{% endif %}."""
{%- if use_jwt %}
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
{% endif %}
{% if use_jwt %}import jwt
{% endif %}from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
{%- if use_jwt %}

from app.core.config import get_settings
{%- endif %}

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

_hasher = PasswordHasher()

# Verified against when the email is unknown, so a login for a missing account
# costs the same as one with a wrong password (no account-existence timing oracle).
DUMMY_HASH = _hasher.hash("dummy-password-used-only-for-timing-equalisation")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """True if the password matches; False on mismatch or a malformed hash (never raises)."""
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False
{%- if use_jwt %}


# --- access tokens ------------------------------------------------------------

ACCESS_TOKEN_TYPE = "access"
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "jti", "type"]


class InvalidTokenError(Exception):
    """The access token is malformed, expired, wrongly signed or not an access token."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    jti: str


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Return a signed HS256 access token and its lifetime in seconds."""
    settings = get_settings()
    lifetime = timedelta(minutes=settings.access_token_expire_minutes)
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "jti": uuid.uuid4().hex,
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + lifetime,
    }
    token = jwt.encode(claims, settings.secret_key, algorithm="HS256")
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str) -> AccessTokenClaims:
    """Verify and decode an access token or raise InvalidTokenError.

    The algorithm is pinned to HS256: the token's own ``alg`` header is never trusted,
    so ``none`` and algorithm-confusion tokens are rejected.
    """
    try:
        payload = jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=["HS256"],
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("not an access token")
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        raise InvalidTokenError("invalid jti")
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except ValueError as exc:
        raise InvalidTokenError("invalid sub") from exc
    return AccessTokenClaims(user_id=user_id, jti=jti)


# --- refresh tokens -------------------------------------------------------------


def generate_refresh_token() -> str:
    """Opaque, high-entropy refresh token; only its hash is ever stored."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """SHA-256 is enough here: the token is random (no dictionary to brute-force)."""
    return hashlib.sha256(token.encode()).hexdigest()
{%- endif %}
